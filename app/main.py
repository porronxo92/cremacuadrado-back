"""
Cremacuadrado API - FastAPI Application Entry Point
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
from uuid import uuid4

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from starlette.middleware.sessions import SessionMiddleware
from sqladmin import Admin

from app.config import settings
from app.limiter import limiter
from app.services import blob_service
from app.api.v1 import router as api_v1_router
from app.models.database import engine, Base
from app.sqladmin_config import (
    AdminAuth,
    UserAdmin, AddressAdmin, PasswordResetTokenAdmin,
    CategoryAdmin, ProductAdmin, ProductVariantAdmin,
    ProductImageAdmin, ProductNutritionAdmin, ReviewAdmin,
    CartAdmin, CartItemAdmin,
    OrderAdmin, OrderItemAdmin, CouponAdmin,
    PaymentIntentAdmin, StripeWebhookEventAdmin, RefundAdmin,
    BlogCategoryAdmin, BlogPostAdmin,
)

# 30 days in seconds — images rarely change
STATIC_CACHE_MAX_AGE = 60 * 60 * 24 * 30


class CachedStaticFiles(StaticFiles):
    """StaticFiles with long-lived Cache-Control headers."""

    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = f"public, max-age={STATIC_CACHE_MAX_AGE}, immutable"
        return response


def _cancel_ghost_orders() -> None:
    """Cancel pending_payment orders that have been waiting longer than the configured threshold."""
    from app.models.order import Order
    from app.models.database import SessionLocal
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.PENDING_ORDER_EXPIRE_MINUTES)
        expired = db.query(Order).filter(
            Order.status == "pending_payment",
            Order.created_at < cutoff,
        ).all()
        if expired:
            for order in expired:
                order.status = "cancelled"
            db.commit()
            print(f"[cleanup] Cancelled {len(expired)} ghost order(s)")
    except Exception as exc:
        db.rollback()
        print(f"[cleanup] Error cancelling ghost orders: {exc}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Start background scheduler for ghost order cleanup (skipped on serverless)
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(_cancel_ghost_orders, "interval", minutes=5, id="ghost_order_cleanup")
        scheduler.start()
        print("[scheduler] Ghost order cleanup running every 5 minutes")
        yield
        scheduler.shutdown(wait=False)
    except Exception as exc:
        print(f"[scheduler] Could not start (serverless?): {exc}")
        yield

    print("Shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="API para el ecommerce de Cremacuadrado - Cremas de pistacho artesanales",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Rate limiting — returns 429 Too Many Requests when limits are exceeded
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# sqladmin — panel de administración en /admin
_admin_auth = AdminAuth(secret_key=settings.SECRET_KEY)
_sqladmin = Admin(
    app,
    engine,
    base_url="/admin",
    title="CremaCuadrado Admin",
    authentication_backend=_admin_auth,
)
_sqladmin.add_view(UserAdmin)
_sqladmin.add_view(AddressAdmin)
_sqladmin.add_view(PasswordResetTokenAdmin)
_sqladmin.add_view(CategoryAdmin)
_sqladmin.add_view(ProductAdmin)
_sqladmin.add_view(ProductVariantAdmin)
_sqladmin.add_view(ProductImageAdmin)
_sqladmin.add_view(ProductNutritionAdmin)
_sqladmin.add_view(ReviewAdmin)
_sqladmin.add_view(CartAdmin)
_sqladmin.add_view(CartItemAdmin)
_sqladmin.add_view(OrderAdmin)
_sqladmin.add_view(OrderItemAdmin)
_sqladmin.add_view(CouponAdmin)
_sqladmin.add_view(PaymentIntentAdmin)
_sqladmin.add_view(StripeWebhookEventAdmin)
_sqladmin.add_view(RefundAdmin)
_sqladmin.add_view(BlogCategoryAdmin)
_sqladmin.add_view(BlogPostAdmin)

# Trusted Host middleware — prevents Host header injection
# In production set ALLOWED_HOSTS=["cremacuadrado-back.vercel.app"] in env vars
if settings.ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins = [
    "https://cremacuadrado-front.vercel.app",
    "http://localhost:4200"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cart-Session"],
)

# sqladmin requiere SessionMiddleware para gestionar la sesión del panel de admin.
# Añadido después de CORS para que sea la capa más externa (procesa los requests primero).
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="sqladmin_session",   # nombre único — no interfiere con cart_session
    max_age=3600 * 8,                    # sesión admin de 8 horas
    https_only=not settings.DEBUG,       # cookie segura en producción
    same_site="lax",
)


@app.middleware("http")
async def cart_session_middleware(request: Request, call_next):
    """
    Accept cart_session from both cookie and header (for cross-domain scenarios).
    Expose X-Cart-Session header in response for frontend to cache.
    """
    # Read session from cookie or header (header takes precedence for cross-origin)
    session_from_header = request.headers.get("X-Cart-Session")
    session_from_cookie = request.cookies.get("cart_session")
    cart_session = session_from_header or session_from_cookie

    # Store in request state for use by endpoints
    request.state.cart_session = cart_session

    response = await call_next(request)

    # Expose the session in response header if present
    if cart_session:
        response.headers["X-Cart-Session"] = cart_session

    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if not settings.DEBUG:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response

# Include API routers
app.include_router(api_v1_router, prefix="/api/v1")

# Serve static files with long-term browser cache (30 days)
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/static", CachedStaticFiles(directory=str(static_path)), name="static")


_ADMIN_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_ADMIN_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _admin_safe_dest(dest_path: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9 _\-/.]", "", dest_path).strip("/")
    if ".." in clean:
        raise ValueError("Ruta no permitida")
    return clean or "misc"


@app.post("/admin-upload")
async def admin_upload(
    request: Request,
    file: UploadFile = File(...),
    dest_path: str = Form("misc"),
):
    """Upload an image from the SQLAdmin panel (session-authenticated) to Vercel Blob."""
    if request.session.get("token") != "authenticated":
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ADMIN_ALLOWED_EXT:
        return JSONResponse(
            {"error": f"Extensión no permitida. Usa: {', '.join(_ADMIN_ALLOWED_EXT)}"},
            status_code=400,
        )

    try:
        clean = _admin_safe_dest(dest_path)
    except ValueError:
        return JSONResponse({"error": "Ruta no permitida"}, status_code=400)

    # Read with 10 MB cap
    content = await file.read(_ADMIN_MAX_BYTES + 1)
    if len(content) > _ADMIN_MAX_BYTES:
        return JSONResponse({"error": "El archivo supera el límite de 10 MB"}, status_code=413)

    safe_name = re.sub(r"[^a-zA-Z0-9._\-]", "_", os.path.basename(file.filename or "file"))
    filename = f"{uuid4().hex[:8]}_{safe_name}"
    pathname = f"images/{clean}/{filename}"

    try:
        url = await blob_service.upload(content, pathname)
    except Exception as exc:
        return JSONResponse({"error": f"Error subiendo imagen: {exc}"}, status_code=500)

    return {"url": url, "filename": filename}


@app.middleware("http")
async def inject_admin_scripts(request: Request, call_next):
    """Inject custom JS into every SQLAdmin HTML page."""
    response = await call_next(request)
    if (
        request.url.path.startswith("/admin")
        and "text/html" in response.headers.get("content-type", "")
    ):
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        script = b'<script src="/static/js/admin-extra.js"></script>'
        body = body.replace(b"</body>", script + b"</body>")

        return Response(
            content=body,
            status_code=response.status_code,
            headers={**dict(response.headers), "content-length": str(len(body))},
            media_type="text/html; charset=utf-8",
        )
    return response


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Bienvenido a Cremacuadrado API"}
