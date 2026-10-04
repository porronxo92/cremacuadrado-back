"""
Coupon redemption tracking — which customer used which coupon on which order.

Both helpers are defensive: they run inside a SAVEPOINT so a failure here
(e.g. table not migrated yet) never breaks payment or order processing.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.order import Coupon, CouponRedemption, Order

logger = logging.getLogger("cremacuadrado.coupons")


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
                discount_amount=order.discount or 0,
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
