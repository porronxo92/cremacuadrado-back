"""Stripe payment service."""
import stripe
from typing import Optional
from app.config import settings


def _get_stripe():
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_payment_intent(
    amount: int,
    currency: str,
    order_id: int,
    order_number: str,
    cart_id: int,
    customer_email: Optional[str] = None,
) -> stripe.PaymentIntent:
    s = _get_stripe()
    params: dict = {
        "amount": amount,
        "currency": currency,
        "automatic_payment_methods": {"enabled": True},
        "metadata": {
            "order_id": str(order_id),
            "order_number": order_number,
            "cart_id": str(cart_id),
        },
    }
    if customer_email:
        params["receipt_email"] = customer_email
    return s.PaymentIntent.create(**params)


def retrieve_payment_intent(pi_id: str) -> stripe.PaymentIntent:
    s = _get_stripe()
    return s.PaymentIntent.retrieve(pi_id)


def create_refund(
    payment_intent_id: str,
    amount: Optional[int] = None,
    reason: str = "requested_by_customer",
) -> stripe.Refund:
    s = _get_stripe()
    params: dict = {"payment_intent": payment_intent_id, "reason": reason}
    if amount:
        params["amount"] = amount
    return s.Refund.create(**params)


def verify_webhook_signature(payload: bytes, sig_header: str) -> stripe.Event:
    """Raises stripe.error.SignatureVerificationError if invalid."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
