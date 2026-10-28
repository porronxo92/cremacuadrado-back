"""
Tareas programadas (Vercel Cron). Vercel llama con GET y envía
"Authorization: Bearer <CRON_SECRET>". Sin CRON_SECRET configurado, el endpoint
queda deshabilitado.
"""
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.api.deps import DbSession
from app.config import settings
from app.services.correos.tracking import poll_active_shipments
from app.services.order_cleanup import run_pending_order_jobs
from app.services.retention import purge

logger = logging.getLogger("cremacuadrado.maintenance")

router = APIRouter()


def _check_cron_secret(authorization: str | None) -> None:
    expected = f"Bearer {settings.CRON_SECRET}" if settings.CRON_SECRET else None
    if not expected or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")


@router.get("/purge")
def run_purge(db: DbSession, authorization: str | None = Header(None)):
    """Purga diaria de datos que han superado su plazo de conservación (y pedidos fantasma)."""
    _check_cron_secret(authorization)
    deleted = purge(db)
    jobs = run_pending_order_jobs(db)
    deleted["payment_reminders_sent"] = jobs["reminders_sent"]
    deleted["ghost_orders_cancelled"] = jobs["cancelled"]
    return {"status": "ok", "deleted": deleted}


@router.get("/ghost-orders")
def run_ghost_orders(db: DbSession, authorization: str | None = Header(None)):
    """Recordatorio (24 h) y cancelación (48 h) de pedidos pending_payment. En plan Pro: cron cada 15 min."""
    _check_cron_secret(authorization)
    jobs = run_pending_order_jobs(db)
    return {"status": "ok", **jobs}


@router.get("/poll-shipments")
def run_poll_shipments(authorization: str | None = Header(None)):
    """Sincroniza el seguimiento de Correos de los envíos activos."""
    _check_cron_secret(authorization)
    poll_active_shipments()
    return {"status": "ok"}
