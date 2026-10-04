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
from app.models.payment import PaymentIntent as PaymentIntentModel, StripeWebhookEvent, Refund
from app.services import stripe_service
from app.services.coupon_redemptions import record_redemption, revert_redemption
from app.services.email import EmailService, send_order_confirmation, OrderEmailData
from app.config import settings

router = APIRouter()

HANDLED_EVENTS = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
    "payment_intent.processing",
    "charge.refunded",
    "charge.dispute.created",
    "charge.dispute.closed",
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
        elif event_type == "charge.refunded":
            _handle_charge_refunded(db, data)
        elif event_type == "charge.dispute.created":
            _handle_dispute_created(db, data)
        elif event_type == "charge.dispute.closed":
            _handle_dispute_closed(db, data)

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


def handle_payment_succeeded(db: Session, stripe_pi_id: str) -> None:
    """
    Public entry point for processing a succeeded PaymentIntent.
    Called by both the Stripe webhook handler and the confirmation endpoint
    (fallback for local dev or delayed webhooks).
    Idempotent: skips processing if the order is already paid.
    """
    _handle_payment_succeeded(db, {"id": stripe_pi_id})


def handle_payment_succeeded(db: Session, stripe_pi_id: str) -> None:
    """
    Public entry point for processing a succeeded PaymentIntent.
    Called by both the Stripe webhook handler and the confirmation endpoint
    (fallback for local dev or delayed webhooks).
    Idempotent: skips processing if the order is already paid.
    """
    _handle_payment_succeeded(db, {"id": stripe_pi_id})


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

    # Reduce stock — prefer variant stock (new orders); fall back to product.stock (legacy orders)
    from app.models.product import ProductVariant
    for item in order_with_items.items:
        if item.product_variant_id:
            variant = (
                db.query(ProductVariant)
                .filter(ProductVariant.id == item.product_variant_id)
                .with_for_update()
                .first()
            )
            if variant:
                variant.stock = max(0, variant.stock - item.quantity)
        elif item.product_id:
            product = (
                db.query(Product)
                .filter(Product.id == item.product_id)
                .with_for_update()
                .first()
            )
            if product:
                product.stock = max(0, product.stock - item.quantity)

    # Increment coupon usage
    if order.coupon_code:
        coupon = db.query(Coupon).filter(Coupon.code == order.coupon_code).first()
        if coupon:
            coupon.used_count += 1
        record_redemption(db, order)

    # Clear cart — primary: use cart_id stored in PI metadata.
    # Fallback: clear by user_id (covers orders created before metadata was saved).
    pi_record = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == stripe_pi_id
    ).first()
    cart_cleared = False
    if pi_record and pi_record.metadata_:
        cart_id_str = pi_record.metadata_.get("cart_id")
        if cart_id_str:
            cart = db.query(Cart).filter(Cart.id == int(cart_id_str)).first()
            if cart:
                db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
                cart.coupon_code = None
                cart_cleared = True
                logger.info("Cart cleared via metadata: cart_id=%s order=%s", cart_id_str, order.order_number)
    if not cart_cleared and order.user_id:
        # Fallback: clear the authenticated user's active cart
        cart = db.query(Cart).filter(Cart.user_id == order.user_id).first()
        if cart:
            db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
            cart.coupon_code = None
            logger.info("Cart cleared via user_id fallback: user_id=%s order=%s", order.user_id, order.order_number)

    # Generate Correos shipment (defensive: must NOT break payment processing).
    # While CORREOS_ENABLED=False, skip entirely — no API calls, no mock localizador.
    # The tracking number will be entered manually by an admin once the label is ready.
    tracking_number = None
    if settings.CORREOS_ENABLED:
        try:
            from app.services.correos import create_shipment_for_order
            shipment = create_shipment_for_order(db, order_with_items)
            if shipment and shipment.localizador:
                tracking_number = shipment.localizador
        except Exception as exc:
            logger.error(
                "Correos shipment failed: order=%s error=%s",
                order.order_number, exc, exc_info=True,
            )

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
        sent = send_order_confirmation(OrderEmailData(
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
            tracking_number=tracking_number,
        ))
        if not sent:
            logger.error("Confirmation email failed: order=%s to=%s", order.order_number, customer_email)
    else:
        logger.warning("No customer_email for order=%s (guest_email missing?)", order.order_number)

    # Notify admin of new paid order
    try:
        items_summary = ", ".join(
            f"{i.product_name} ×{i.quantity}" for i in order_with_items.items
        )
        EmailService.send_admin_new_order(
            order_number=order.order_number,
            customer_name=order.shipping_address.get("first_name", "Cliente")
                + " " + order.shipping_address.get("last_name", ""),
            customer_email=order.customer_email or "",
            total=float(order.total),
            items_summary=items_summary,
            shipping_address=order.shipping_address,
            tracking_number=tracking_number,
        )
    except Exception as exc:
        logger.error("Admin new-order email failed: order=%s error=%s", order.order_number, exc)


def _handle_payment_failed(db: Session, data: dict) -> None:
    stripe_pi_id = data["id"]
    order = _get_order_by_pi(db, stripe_pi_id)
    if order and order.status == "pending_payment":
        order.status = "payment_failed"
        logger.warning("Payment failed: order=%s pi=%s", order.order_number, stripe_pi_id)

        customer_email = order.customer_email
        if customer_email:
            error_message = (data.get("last_payment_error") or {}).get("message")
            try:
                EmailService.send_payment_failed_email(
                    to_email=customer_email,
                    order_number=order.order_number,
                    customer_name=order.shipping_address.get("first_name", "Cliente"),
                    error_message=error_message,
                )
            except Exception as exc:
                logger.error("Payment-failed email failed: order=%s error=%s", order.order_number, exc, exc_info=True)
        else:
            logger.warning("No customer_email for order=%s (payment_failed email skipped)", order.order_number)

    _update_pi_status(db, stripe_pi_id, "requires_payment_method")


def _handle_payment_canceled(db: Session, data: dict) -> None:
    stripe_pi_id = data["id"]
    order = _get_order_by_pi(db, stripe_pi_id)
    if order and order.status in ("pending_payment", "payment_failed"):
        order.status = "cancelled"
        logger.info("Payment canceled: order=%s pi=%s", order.order_number, stripe_pi_id)
    _update_pi_status(db, stripe_pi_id, "canceled")


def _restock_order_items(db: Session, order: Order) -> None:
    """Return every item's quantity back to stock (variant or legacy product)."""
    from app.models.product import Product, ProductVariant

    order_with_items = (
        db.query(Order).options(joinedload(Order.items)).filter(Order.id == order.id).first()
    )
    for item in order_with_items.items:
        if item.product_variant_id:
            variant = (
                db.query(ProductVariant)
                .filter(ProductVariant.id == item.product_variant_id)
                .with_for_update()
                .first()
            )
            if variant:
                variant.stock += item.quantity
        elif item.product_id:
            product = (
                db.query(Product)
                .filter(Product.id == item.product_id)
                .with_for_update()
                .first()
            )
            if product:
                product.stock += item.quantity


def _handle_charge_refunded(db: Session, data: dict) -> None:
    """Handle charge.refunded — covers both full and partial refunds."""
    stripe_pi_id = data.get("payment_intent")
    if not stripe_pi_id:
        logger.warning("charge.refunded without payment_intent, charge=%s", data.get("id"))
        return

    order = _get_order_by_pi(db, stripe_pi_id)
    if not order:
        logger.warning("charge.refunded: no order found for pi=%s", stripe_pi_id)
        return

    amount_refunded = data.get("amount_refunded", 0)
    amount_total = data.get("amount", 0)
    is_full_refund = amount_total > 0 and amount_refunded >= amount_total

    # Persist each new refund object from the charge (idempotent on stripe_refund_id)
    for stripe_refund in (data.get("refunds") or {}).get("data", []):
        existing = db.query(Refund).filter(Refund.stripe_refund_id == stripe_refund["id"]).first()
        if existing:
            existing.status = stripe_refund["status"]
            continue
        pi_record = db.query(PaymentIntentModel).filter(
            PaymentIntentModel.stripe_payment_intent_id == stripe_pi_id
        ).first()
        db.add(Refund(
            order_id=order.id,
            payment_intent_id=pi_record.id if pi_record else None,
            stripe_refund_id=stripe_refund["id"],
            amount=stripe_refund["amount"],
            reason=stripe_refund.get("reason"),
            status=stripe_refund["status"],
        ))

    if order.status != "refunded":
        order.status = "refunded" if is_full_refund else "partially_refunded"
        logger.info(
            "Order refunded: order=%s pi=%s amount=%s full=%s",
            order.order_number, stripe_pi_id, amount_refunded, is_full_refund,
        )
        if is_full_refund:
            _restock_order_items(db, order)
            revert_redemption(db, order)

        customer_email = order.customer_email
        if customer_email:
            try:
                EmailService.send_order_status_update_email(
                    to_email=customer_email,
                    order_number=order.order_number,
                    customer_name=order.shipping_address.get("first_name", "Cliente"),
                    new_status="refunded",
                )
            except Exception as exc:
                logger.error("Refund email failed: order=%s error=%s", order.order_number, exc, exc_info=True)
        try:
            EmailService.send_admin_status_change(order_number=order.order_number, new_status="refunded")
        except Exception as exc:
            logger.error("Admin refund notification failed: order=%s error=%s", order.order_number, exc)


def _handle_dispute_created(db: Session, data: dict) -> None:
    """Handle charge.dispute.created — a chargeback was opened by the customer's bank."""
    stripe_pi_id = data.get("payment_intent")
    order = _get_order_by_pi(db, stripe_pi_id) if stripe_pi_id else None
    if not order:
        logger.warning("charge.dispute.created: no order found for pi=%s", stripe_pi_id)
        return

    order.admin_notes = (order.admin_notes or "") + (
        f"\n[Disputa Stripe] id={data.get('id')} motivo={data.get('reason')} importe={data.get('amount')}"
    )
    logger.warning(
        "Dispute opened: order=%s pi=%s dispute=%s reason=%s",
        order.order_number, stripe_pi_id, data.get("id"), data.get("reason"),
    )
    try:
        EmailService.send_admin_status_change(order_number=order.order_number, new_status="disputed")
    except Exception as exc:
        logger.error("Admin dispute notification failed: order=%s error=%s", order.order_number, exc)


def _handle_dispute_closed(db: Session, data: dict) -> None:
    """Handle charge.dispute.closed — the dispute was resolved (won/lost/warning_closed)."""
    stripe_pi_id = data.get("payment_intent")
    order = _get_order_by_pi(db, stripe_pi_id) if stripe_pi_id else None
    if not order:
        logger.warning("charge.dispute.closed: no order found for pi=%s", stripe_pi_id)
        return

    outcome = data.get("status", "unknown")
    order.admin_notes = (order.admin_notes or "") + f"\n[Disputa Stripe] resuelta: {outcome}"
    logger.info("Dispute closed: order=%s pi=%s outcome=%s", order.order_number, stripe_pi_id, outcome)

    if outcome == "lost" and order.status != "refunded":
        order.status = "refunded"
        _restock_order_items(db, order)

    try:
        EmailService.send_admin_status_change(order_number=order.order_number, new_status=f"disputa-{outcome}")
    except Exception as exc:
        logger.error("Admin dispute-closed notification failed: order=%s error=%s", order.order_number, exc)
