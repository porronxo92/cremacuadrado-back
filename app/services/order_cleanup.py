"""
Limpieza de pedidos «fantasma» (pending_payment que nunca se pagaron).

Se ejecuta desde Vercel Cron (GET /api/v1/maintenance/ghost-orders y dentro de la
purga diaria) y, solo en desarrollo local, desde APScheduler (app/main.py).
Un pedido cancelado todavía puede recibir el pago: el webhook de Stripe acepta
el estado `cancelled`.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.order import Order

logger = logging.getLogger("cremacuadrado.cleanup")


def cancel_ghost_orders(db: Session) -> int:
    """Cancela los pedidos pending_payment más antiguos que el umbral. Hace commit."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=settings.PENDING_ORDER_EXPIRE_MINUTES)).replace(tzinfo=None)
    count = db.query(Order).filter(
        Order.status == "pending_payment",
        Order.created_at < cutoff,
    ).update({Order.status: "cancelled"}, synchronize_session=False)
    db.commit()
    if count:
        logger.info("Ghost orders cancelled: %d", count)
    return count
