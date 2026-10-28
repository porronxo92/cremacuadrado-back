"""Webhook de Stripe: eventos reales de stripe-python (no dicts) y reembolsos sin `refunds` en el charge."""
import json
from decimal import Decimal

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.order import Order
from app.models.payment import PaymentIntent as PIModel, Refund, StripeWebhookEvent
from app.services import stripe_service
from app.services.email import EmailService


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def client(db, monkeypatch):
    PIModel.__table__.create(db.get_bind(), checkfirst=True)
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


@pytest.fixture()
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(EmailService, "send_order_status_update_email",
                        staticmethod(lambda **kw: sent.append(kw["new_status"])))
    monkeypatch.setattr(EmailService, "send_admin_status_change", staticmethod(lambda **kw: None))
    return sent


def _post_event(client, monkeypatch, event: dict):
    """Firma «verificada» devolviendo un stripe.Event real, como construct_event en producción."""
    monkeypatch.setattr(stripe_service, "verify_webhook_signature",
                        lambda payload, sig: stripe.Event.construct_from(json.loads(payload), "sk_test"))
    return client.post("/api/v1/webhooks/stripe", content=json.dumps(event),
                       headers={"Stripe-Signature": "t=1,v1=x", "Content-Type": "application/json"})


def _paid_order(db, status="paid"):
    order = Order(order_number="CC-W1", status=status, subtotal=Decimal("5.85"), shipping_cost=Decimal("0"),
                  discount=Decimal("0"), tax=Decimal("0"), total=Decimal("5.85"), guest_email="ana@example.com",
                  payment_intent_id="pi_w1", shipping_address={"first_name": "Ana", "postal_code": "13005"})
    db.add(order)
    db.flush()
    db.add(PIModel(order_id=order.id, stripe_payment_intent_id="pi_w1", stripe_client_secret="s", amount=585))
    db.commit()
    return order


def _charge_refunded(amount_refunded=585, refunds=None):
    charge = {"id": "ch_w1", "object": "charge", "payment_intent": "pi_w1", "amount": 585,
              "amount_refunded": amount_refunded}
    if refunds is not None:
        charge["refunds"] = {"object": "list", "data": refunds}
    return {"id": "evt_w1", "object": "event", "type": "charge.refunded", "api_version": "2024-04-10",
            "data": {"object": charge}}


def test_charge_refunded_without_refunds_list_marks_order_refunded(client, db, monkeypatch, emails):
    order = _paid_order(db)
    monkeypatch.setattr(stripe_service, "list_charge_refunds", lambda charge_id: [
        {"id": "re_w1", "object": "refund", "amount": 585, "status": "succeeded", "reason": "requested_by_customer"}])
    r = _post_event(client, monkeypatch, _charge_refunded())
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    db.expire_all()
    assert db.query(Order).get(order.id).status == "refunded"
    refund = db.query(Refund).one()
    assert refund.stripe_refund_id == "re_w1" and refund.amount == 585 and refund.status == "succeeded"
    assert emails == ["refunded"]
    event = db.query(StripeWebhookEvent).one()
    assert event.processed and event.error is None


def test_partial_refund_from_payload_list(client, db, monkeypatch, emails):
    order = _paid_order(db)
    monkeypatch.setattr(stripe_service, "list_charge_refunds",
                        lambda charge_id: pytest.fail("no debe llamar a Stripe si el evento trae los refunds"))
    r = _post_event(client, monkeypatch, _charge_refunded(
        amount_refunded=200, refunds=[{"id": "re_p1", "amount": 200, "status": "succeeded"}]))
    assert r.status_code == 200
    db.expire_all()
    assert db.query(Order).get(order.id).status == "partially_refunded"
    assert db.query(Refund).one().amount == 200


def test_payment_failed_event_is_processed(client, db, monkeypatch):
    _paid_order(db, status="pending_payment")
    r = _post_event(client, monkeypatch, {
        "id": "evt_f1", "object": "event", "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_w1", "object": "payment_intent", "status": "requires_payment_method",
                            "last_payment_error": {"message": "Tarjeta rechazada"}}}})
    assert r.status_code == 200
    db.expire_all()
    event = db.query(StripeWebhookEvent).one()
    assert event.processed and event.error is None
