"""
Shipment orchestration — creates a Correos shipment for a paid order.

Designed to be called from the Stripe webhook handler right after an order is
marked paid. It is fully defensive: any failure is recorded on the Shipment row
(status='failed', error=...) but never raised, so it cannot break payment
processing or revert the order's 'paid' state.
"""
import logging

from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.order import Order
from app.models.shipment import Shipment
from app.services.correos.preregister import preregister_shipment

logger = logging.getLogger("cremacuadrado.correos")


def _compute_weight_grams(order: Order) -> int:
    """Sum variant weight × quantity across order items, with a safe fallback."""
    total = 0
    for item in order.items:
        variant = item.variant
        if variant and variant.weight_grams:
            total += variant.weight_grams * item.quantity
    if total <= 0:
        total = settings.CORREOS_DEFAULT_WEIGHT_GRAMS
    return total


def create_shipment_for_order(db: Session, order: Order) -> Shipment | None:
    """
    Create (or return existing) Shipment for an order.

    Returns the Shipment (with .localizador set on success) or None if creation
    failed before a row could be persisted. The Shipment is added to the given
    session but NOT committed — the caller's transaction owns the commit.
    """
    # Idempotency: one shipment per order
    existing = (
        db.query(Shipment)
        .filter(Shipment.order_id == order.id)
        .first()
    )
    if existing:
        logger.info("Shipment already exists for order=%s (id=%s)", order.order_number, existing.id)
        return existing

    # Ensure items + variants are loaded for weight computation
    order_full = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order.id)
        .first()
    ) or order

    weight_grams = _compute_weight_grams(order_full)

    shipment = Shipment(
        order_id=order.id,
        service_code=settings.CORREOS_SERVICE_CODE,
        weight_grams=weight_grams,
        status="created",
    )

    try:
        result = preregister_shipment(order_full, weight_grams)
        shipment.localizador = result["localizador"]
        shipment.correos_request = result.get("request")
        shipment.correos_response = result.get("response")
        shipment.status = "label_created"

        # Surface tracking on the order for emails / admin / customer account
        order.tracking_number = shipment.localizador
        order.shipping_status = "label_created"
    except Exception as exc:
        shipment.status = "failed"
        shipment.error = str(exc)
        order.shipping_status = "failed"
        logger.error(
            "Correos shipment creation failed: order=%s error=%s",
            order.order_number, exc, exc_info=True,
        )

    db.add(shipment)
    return shipment
