"""
Coupon redemption tracking — which customer used which coupon on which order.

Both helpers are defensive: they run inside a SAVEPOINT so a failure here
(e.g. table not migrated yet) never breaks payment or order processing.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.order import Coupon, CouponRedemption, Order
from app.services.pricing import FREE_SHIPPING_TYPE, shipping_zone

logger = logging.getLogger("cremacuadrado.coupons")


PAID_STATUSES = ("paid", "processing", "shipped", "delivered")


def user_already_used(db: Session, user_id: int, code: str) -> bool:
    """True if this user already has a paid order with the coupon (one use per customer)."""
    return db.query(Order.id).filter(
        Order.user_id == user_id,
        func.upper(Order.coupon_code) == code.strip().upper(),
        Order.status.in_(PAID_STATUSES),
    ).first() is not None


def _redeemed_amount(order: Order, coupon: Coupon | None):
    """
    Importe descontado. En free_shipping el pedido guarda discount=0 y envío=0, así que
    el envío anulado es siempre el coste fijo de envío (el cupón solo aplica si lo habría).
    """
    if coupon is not None and coupon.discount_type == FREE_SHIPPING_TYPE:
        postal = (order.shipping_address or {}).get("postal_code")
        if shipping_zone(postal) == "baleares":
            return Decimal(str(settings.SHIPPING_COST_BALEARES))
        return Decimal(str(settings.SHIPPING_COST))
    return order.discount or 0


def record_redemption(db: Session, order: Order) -> None:
    """Create the redemption row for a paid order that used a coupon (idempotent per order)."""
    if not order.coupon_code:
        return
    try:
        with db.begin_nested():
            existing = db.query(CouponRedemption).filter(CouponRedemption.order_id == order.id).first()
            if existing:
                existing.reverted_at = None
                return
            code = order.coupon_code.strip().upper()
            coupon = db.query(Coupon).filter(func.upper(Coupon.code) == code).first()
            db.add(CouponRedemption(
                coupon_id=coupon.id if coupon else None,
                coupon_code=code,
                order_id=order.id,
                user_id=order.user_id,
                email=order.customer_email,
                discount_amount=_redeemed_amount(order, coupon),
            ))
    except Exception as exc:
        logger.error("Coupon redemption not recorded: order=%s error=%s", order.order_number, exc, exc_info=True)


def revert_redemption(db: Session, order: Order) -> None:
    """Mark the order's redemption as reverted (order cancelled or fully refunded)."""
    if not order.coupon_code:
        return
    try:
        with db.begin_nested():
            db.query(CouponRedemption).filter(
                CouponRedemption.order_id == order.id,
                CouponRedemption.reverted_at.is_(None),
            ).update({"reverted_at": datetime.now(timezone.utc)}, synchronize_session=False)
    except Exception as exc:
        logger.error("Coupon redemption not reverted: order=%s error=%s", order.order_number, exc, exc_info=True)
