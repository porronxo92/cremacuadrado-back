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
from app.services.retention import purge

logger = logging.getLogger("cremacuadrado.maintenance")

router = APIRouter()


def _check_cron_secret(authorization: str | None) -> None:
    expected = f"Bearer {settings.CRON_SECRET}" if settings.CRON_SECRET else None
    if not expected or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")


@router.get("/purge")
def run_purge(db: DbSession, authorization: str | None = Header(None)):
    """Purga diaria de datos que han superado su plazo de conservación."""
    _check_cron_secret(authorization)
    return {"status": "ok", "deleted": purge(db)}
