"""
Cupón aplicado o quitado en el checkout, después de crear el pedido: pre-confirm
toma el cupón de la cesta del cliente y recalcula pedido y PaymentIntent.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.config import settings
from app.main import app
from app.models.cart import Cart
from app.models.database import get_db
from app.models.order import Coupon, Order
from app.models.payment import PaymentIntent as PIModel
from app.models.user import User
from app.services import stripe_service


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


@pytest.fixture()
def stripe_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe_service, "retrieve_payment_intent",
                        lambda pi: SimpleNamespace(status="requires_payment_method"))
    monkeypatch.setattr(stripe_service, "modify_payment_intent_amount",
                        lambda pi, amount: calls.append((pi, amount)))
    return calls


def _setup(db, *, user=True, order_coupon=None, cart_coupon=None, discount="0", total="34.95"):
    """Pedido de 30 € + 4,95 € de envío a península, con su PaymentIntent y la cesta del cliente."""
    PIModel.__table__.create(db.get_bind(), checkfirst=True)
    db.add(Coupon(code="DIEZ", discount_type="percent", discount_value=Decimal("10")))
    u = None
    if user:
        u = User(email="ana@example.com", first_name="Ana", last_name="P", password_hash="x")
        db.add(u)
        db.flush()
        db.add(Cart(user_id=u.id, coupon_code=cart_coupon))
    order = Order(order_number="CC-C1", status="pending_payment", user_id=u.id if u else None,
                  guest_email=None if u else "ana@example.com",
                  subtotal=Decimal("30"), shipping_cost=Decimal("4.95"), discount=Decimal(discount),
                  tax=Decimal("0"), total=Decimal(total), coupon_code=order_coupon,
                  payment_intent_id="pi_c1", shipping_address={"postal_code": "13005"})
    db.add(order)
    db.flush()
    db.add(PIModel(order_id=order.id, stripe_payment_intent_id="pi_c1", stripe_client_secret="s",
                   amount=int(Decimal(total) * 100)))
    db.commit()
    return u


def _pre(client):
    return client.post("/api/v1/checkout/pre-confirm", json={
        "order_number": "CC-C1", "payment_intent_id": "pi_c1", "accept_terms": True})


def test_coupon_applied_in_checkout_lowers_amount(client, db, stripe_calls):
    _setup(db, cart_coupon="DIEZ")
    r = _pre(client)
    assert r.status_code == 200
    assert Decimal(str(r.json()["total"])) == Decimal("31.95")  # 30 − 3 + 4,95
    assert stripe_calls == [("pi_c1", 3195)]
    db.expire_all()
    order = db.query(Order).one()
    assert order.coupon_code == "DIEZ" and order.discount == Decimal("3.00")
    assert db.query(PIModel).one().amount == 3195


def test_coupon_removed_in_checkout_restores_amount(client, db, stripe_calls):
    _setup(db, order_coupon="DIEZ", cart_coupon=None, discount="3", total="31.95")
    assert _pre(client).status_code == 200
    assert stripe_calls == [("pi_c1", 3495)]
    db.expire_all()
    order = db.query(Order).one()
    assert order.coupon_code is None and order.total == Decimal("34.95")


def test_same_coupon_does_not_touch_stripe(client, db, stripe_calls):
    _setup(db, order_coupon="DIEZ", cart_coupon="DIEZ", discount="3", total="31.95")
    assert _pre(client).status_code == 200
    assert stripe_calls == []


def test_coupon_already_used_by_customer_is_ignored(client, db, stripe_calls):
    u = _setup(db, cart_coupon="DIEZ")
    db.add(Order(order_number="CC-OLD", status="paid", user_id=u.id, subtotal=Decimal("30"),
                 shipping_cost=Decimal("0"), discount=Decimal("3"), tax=Decimal("0"),
                 total=Decimal("27"), coupon_code="DIEZ", shipping_address={"postal_code": "13005"}))
    db.commit()
    assert _pre(client).status_code == 200
    assert stripe_calls == []
    db.expire_all()
    assert db.query(Order).filter_by(order_number="CC-C1").one().coupon_code is None


def test_unknown_coupon_in_cart_is_ignored(client, db, stripe_calls):
    _setup(db, cart_coupon="NOEXISTE")
    assert _pre(client).status_code == 200
    assert stripe_calls == []


def test_guest_order_never_gets_coupon(client, db, stripe_calls):
    _setup(db, user=False)
    assert _pre(client).status_code == 200
    assert stripe_calls == []
    db.expire_all()
    assert db.query(Order).one().coupon_code is None
