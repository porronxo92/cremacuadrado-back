"""
Stripe webhook handler.
Receives payment events from Stripe and updates order state canonically.
"""
import json
import logging
from datetime import datetime, timezone

import stripe

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession

logger = logging.getLogger("cremacuadrado.webhooks")
from app.models.order import Order, OrderItem, Coupon
from app.models.cart import Cart, CartItem
from app.models.payment import PaymentIntent as PaymentIntentModel, StripeWebhookEvent
from app.services import stripe_service
from app.services.email import EmailService, send_order_confirmation, OrderEmailData

router = APIRouter()

HANDLED_EVENTS = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
    "payment_intent.processing",
}


@router.post("/stripe")
async def stripe_webhook(request: Request, db: DbSession):
    """
    Receives Stripe webhook events.
    Always returns 200 after signature verification so Stripe doesn't retry on internal errors.
    Idempotency is enforced via stripe_event_id UNIQUE constraint.
    """
    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # 1. Verify Stripe signature — return 400 if invalid (Stripe will retry)
    try:
        event = stripe_service.verify_webhook_signature(payload_bytes, sig_header)
    except (stripe.error.SignatureVerificationError, RuntimeError) as exc:
        logger.warning("Stripe webhook signature invalid: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {exc}")

    event_id: str = event["id"]
    event_type: str = event["type"]

    # 2. Parse payload to a plain dict for JSONB storage
    payload_dict: dict = json.loads(payload_bytes.decode("utf-8"))

    # 3. Idempotency: skip events we've already fully processed
    existing: StripeWebhookEvent | None = db.query(StripeWebhookEvent).filter(
        StripeWebhookEvent.stripe_event_id == event_id
    ).first()

    if existing and existing.processed:
        return {"status": "already_processed"}

    # 4. Persist the raw event in its own transaction (audit log, idempotency key)
    if not existing:
        existing = StripeWebhookEvent(
            stripe_event_id=event_id,
            event_type=event_type,
            payload=payload_dict,
        )
        db.add(existing)
        try:
            db.commit()
        except Exception:
            # Duplicate insert from concurrent request — load the existing record
            db.rollback()
            existing = db.query(StripeWebhookEvent).filter(
                StripeWebhookEvent.stripe_event_id == event_id
            ).first()
            if not existing or existing.processed:
                return {"status": "already_processed"}

    # 5. Dispatch to handler
    if event_type not in HANDLED_EVENTS:
        logger.debug("Stripe event ignored: %s id=%s", event_type, event_id)
        return {"status": "ignored"}

    logger.info("Stripe event received: %s id=%s", event_type, event_id)

    try:
        data: dict = event["data"]["object"]

        if event_type == "payment_intent.succeeded":
            _handle_payment_succeeded(db, data)
        elif event_type == "payment_intent.payment_failed":
            _handle_payment_failed(db, data)
        elif event_type == "payment_intent.canceled":
            _handle_payment_canceled(db, data)
        elif event_type == "payment_intent.processing":
            _update_pi_status(db, data["id"], "processing")

        existing.processed = True
        existing.processed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Stripe event processed: %s id=%s", event_type, event_id)
        return {"status": "ok"}

    except Exception as exc:
        db.rollback()
        logger.error(
            "Stripe event handler failed: %s id=%s error=%s",
            event_type, event_id, exc,
            exc_info=True,
        )
        try:
            failed_event = db.query(StripeWebhookEvent).filter(
                StripeWebhookEvent.stripe_event_id == event_id
            ).first()
            if failed_event:
                failed_event.error = str(exc)
                db.commit()
        except Exception:
            pass
        return {"status": "error_logged", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------

def _get_order_by_pi(db: Session, stripe_pi_id: str) -> Order | None:
    pi = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == stripe_pi_id
    ).first()
    if not pi:
        return None
    return db.query(Order).options(joinedload(Order.user)).filter(Order.id == pi.order_id).first()


def _update_pi_status(db: Session, stripe_pi_id: str, new_status: str) -> None:
    pi = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == stripe_pi_id
    ).first()
    if pi:
        pi.status = new_status
        pi.updated_at = datetime.now(timezone.utc)


def _handle_payment_succeeded(db: Session, data: dict) -> None:
    from app.models.product import Product

    stripe_pi_id = data["id"]
    order = _get_order_by_pi(db, stripe_pi_id)
    if not order or order.status == "paid":
        logger.info("payment_intent.succeeded skipped pi=%s (order already paid or not found)", stripe_pi_id)
        return

    logger.info("Order paid: order=%s pi=%s total=%s", order.order_number, stripe_pi_id, order.total)

    # Mark order paid
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    _update_pi_status(db, stripe_pi_id, "succeeded")

    # Load items
    order_with_items = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order.id)
        .first()
    )

    # Reduce stock
    for item in order_with_items.items:
        if item.product_id:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            if product:
                product.stock = max(0, product.stock - item.quantity)

    # Increment coupon usage
    if order.coupon_code:
        coupon = db.query(Coupon).filter(Coupon.code == order.coupon_code).first()
        if coupon:
            coupon.used_count += 1

    # Clear cart (cart_id is stored in PI metadata)
    pi_record = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == stripe_pi_id
    ).first()
    if pi_record and pi_record.metadata_:
        cart_id_str = pi_record.metadata_.get("cart_id")
        if cart_id_str:
            cart = db.query(Cart).filter(Cart.id == int(cart_id_str)).first()
            if cart:
                db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
                cart.coupon_code = None

    # Send confirmation email with full order data
    customer_email = order.customer_email
    if customer_email:
        items_data = [
            {
                "name": i.product_name,
                "qty": i.quantity,
                "unit_price": float(i.unit_price),
                "total": float(i.total),
            }
            for i in order_with_items.items
        ]
        send_order_confirmation(OrderEmailData(
            to_email=customer_email,
            customer_name=order.shipping_address.get("first_name", "Cliente"),
            order_number=order.order_number,
            order_date=order.paid_at or datetime.now(timezone.utc),
            items=items_data,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            discount=order.discount,
            total=order.total,
            shipping_address=order.shipping_address,
            coupon_code=order.coupon_code,
            customer_notes=order.customer_notes,
        ))


def _handle_payment_failed(db: Session, data: dict) -> None:
    stripe_pi_id = data["id"]
    order = _get_order_by_pi(db, stripe_pi_id)
    if order and order.status == "pending_payment":
        order.status = "payment_failed"
        logger.warning("Payment failed: order=%s pi=%s", order.order_number, stripe_pi_id)
    _update_pi_status(db, stripe_pi_id, "requires_payment_method")


def _handle_payment_canceled(db: Session, data: dict) -> None:
    stripe_pi_id = data["id"]
    order = _get_order_by_pi(db, stripe_pi_id)
    if order and order.status in ("pending_payment", "payment_failed"):
        order.status = "cancelled"
        logger.info("Payment canceled: order=%s pi=%s", order.order_number, stripe_pi_id)
    _update_pi_status(db, stripe_pi_id, "canceled")
