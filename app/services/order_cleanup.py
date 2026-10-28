"""
Pedidos «fantasma» (pending_payment que el cliente abandonó).

Flujo: a las PENDING_ORDER_REMINDER_HOURS (24 h) se envía un recordatorio a los
clientes registrados; a las PENDING_ORDER_EXPIRE_HOURS (48 h) se cancelan. Para
clientes registrados la cancelación exige además que el recordatorio se enviara
hace PENDING_ORDER_MIN_HOURS_AFTER_REMINDER (12 h) o más, así siempre tienen
margen aunque el cron sea diario. Los invitados solo siguen la regla de 48 h.

Se ejecuta desde Vercel Cron (GET /api/v1/maintenance/ghost-orders y dentro de la
purga diaria) y, solo en desarrollo local, desde APScheduler (app/main.py).
Un pedido cancelado todavía puede recibir el pago: el webhook de Stripe acepta
el estado `cancelled`.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.order import Order
from app.services import stripe_service
from app.services.email import EmailService
from app.utils.site import use_site_url

logger = logging.getLogger("cremacuadrado.cleanup")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def send_pending_payment_reminders(db: Session) -> int:
    """Envía un único recordatorio a los pedidos pending_payment de clientes registrados.

    Marca payment_reminder_sent_at aunque el envío falle (no reintenta en bucle ni
    bloquea la cancelación). Hace commit. Devuelve los emails enviados.
    """
    cutoff = _utcnow() - timedelta(hours=settings.PENDING_ORDER_REMINDER_HOURS)
    orders = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.user))
        .filter(
            Order.status == "pending_payment",
            Order.user_id.isnot(None),
            Order.created_at < cutoff,
            Order.payment_reminder_sent_at.is_(None),
        )
        .all()
    )
    sent = 0
    for order in orders:
        order.payment_reminder_sent_at = _utcnow()
        user = order.user
        if not user or not user.email or not user.is_active:
            continue
        try:
            with use_site_url(order.site_url):
                ok = EmailService.send_pending_payment_reminder(
                    to_email=user.email,
                    first_name=user.first_name or "",
                    order_number=order.order_number,
                    items=[
                        {"name": i.product_name, "qty": i.quantity, "total": i.total}
                        for i in order.items
                    ],
                    total=order.total,
                )
        except Exception as exc:
            ok = False
            logger.error("Pending payment reminder failed: order=%s to=%s err=%s",
                         order.order_number, user.email, exc)
        if ok:
            sent += 1
        else:
            logger.warning("Pending payment reminder not sent: order=%s to=%s",
                           order.order_number, user.email)
    db.commit()
    if sent:
        logger.info("Pending payment reminders sent: %d", sent)
    return sent


def cancel_ghost_orders(db: Session) -> int:
    """Cancela los pending_payment caducados (y su PaymentIntent, best-effort). Hace commit."""
    now = _utcnow()
    expire_cutoff = now - timedelta(hours=settings.PENDING_ORDER_EXPIRE_HOURS)
    reminder_cutoff = now - timedelta(hours=settings.PENDING_ORDER_MIN_HOURS_AFTER_REMINDER)
    orders = db.query(Order).filter(
        Order.status == "pending_payment",
        Order.created_at < expire_cutoff,
        or_(
            Order.user_id.is_(None),
            and_(
                Order.payment_reminder_sent_at.isnot(None),
                Order.payment_reminder_sent_at <= reminder_cutoff,
            ),
        ),
    ).all()
    pi_ids = []
    for order in orders:
        order.status = "cancelled"
        if order.payment_intent_id:
            pi_ids.append(order.payment_intent_id)
    db.commit()
    for pi_id in pi_ids:
        stripe_service.cancel_payment_intent_best_effort(pi_id)
    if orders:
        logger.info("Ghost orders cancelled: %d", len(orders))
    return len(orders)


def run_pending_order_jobs(db: Session) -> dict:
    """Primero recordatorios, luego cancelación."""
    reminders = send_pending_payment_reminders(db)
    cancelled = cancel_ghost_orders(db)
    return {"reminders_sent": reminders, "cancelled": cancelled}
