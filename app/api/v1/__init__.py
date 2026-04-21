"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.products import router as products_router
from app.api.v1.cart import router as cart_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.orders import router as orders_router
from app.api.v1.users import router as users_router
from app.api.v1.blog import router as blog_router
from app.api.v1.admin import router as admin_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(products_router, prefix="/products", tags=["Products"])
router.include_router(cart_router, prefix="/cart", tags=["Cart"])
router.include_router(checkout_router, prefix="/checkout", tags=["Checkout"])
router.include_router(orders_router, prefix="/orders", tags=["Orders"])
router.include_router(users_router, prefix="/users", tags=["Users"])
router.include_router(blog_router, prefix="/blog", tags=["Blog"])
router.include_router(admin_router, prefix="/admin", tags=["Admin"])
