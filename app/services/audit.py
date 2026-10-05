"""
Registro de accesos del personal a datos personales (RGPD art. 32).

Cada petición al API de administración (/api/v1/admin/...) y cada intento de
login en el panel SQLAdmin queda en admin_audit_log. Se escribe con su propia
sesión para no interferir con la transacción de la petición.
"""
import logging
from typing import Optional

from fastapi import Request

from app.limiter import _get_real_ip
from app.models.compliance import AdminAuditLog
from app.models.database import SessionLocal

logger = logging.getLogger("cremacuadrado.audit")


def write(
    *,
    action: str,
    path: str,
    request: Optional[Request] = None,
    admin_user_id: Optional[int] = None,
    admin_email: Optional[str] = None,
    status_code: Optional[int] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(AdminAuditLog(
            admin_user_id=admin_user_id,
            admin_email=admin_email,
            action=action[:20],
            path=path[:500],
            query=(str(request.url.query)[:500] or None) if request is not None else None,
            status_code=status_code,
            ip=(_get_real_ip(request)[:45] if request is not None else None),
            user_agent=(request.headers.get("user-agent", "")[:255] or None) if request is not None else None,
        ))
        db.commit()
    except Exception as exc:  # la auditoría nunca debe tumbar la petición
        db.rollback()
        logger.error("Audit log write failed: %s", exc)
    finally:
        db.close()


async def audit_admin_requests(request: Request, call_next):
    """Middleware: registra las peticiones al API de admin con el resultado."""
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/admin"):
        admin = getattr(request.state, "audit_admin", None) or getattr(request.state, "audit_denied_user", None)
        if admin or response.status_code in (401, 403):
            write(
                action=request.method,
                path=request.url.path,
                request=request,
                admin_user_id=admin[0] if admin else None,
                admin_email=admin[1] if admin else None,
                status_code=response.status_code,
            )
    return response
