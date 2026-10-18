"""Zonas de envío en la API de checkout: Baleares con tarifa, Canarias/Ceuta/Melilla bloqueadas."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.v1.checkout import QUOTE_ZONE_ERROR
from app.config import settings
from app.main import app
from app.models.cart import Cart, CartItem
from app.models.database import get_db
from app.models.order import Order
from app.utils.spain import PROVINCES
from app.models.product import Product, ProductVariant


@pytest.fixture()
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


def _guest_cart(db):
    product = Product(slug="p", name="P")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, format="200g", weight_grams=200,
                             price=Decimal("30.00"), stock=50)
    db.add(variant)
    db.flush()
    cart = Cart(session_id="sess-1")
    db.add(cart)
    db.flush()
    db.add(CartItem(cart_id=cart.id, product_id=product.id, product_variant_id=variant.id,
                    quantity=1, price_at_add=Decimal("30.00")))
    db.commit()


def _payload(cp):
    return {
        "guest_email": "ana@example.com",
        "shipping_address": {"first_name": "Ana", "last_name": "Pérez", "street": "Calle 1",
                             "city": "X", "province": PROVINCES[cp[:2]], "postal_code": cp, "phone": "600123456"},
    }


HEADERS = {"X-Cart-Session": "sess-1"}


@pytest.mark.parametrize("cp", ["35001", "38001", "51001", "52001"])
def test_payment_intent_blocked_for_quote_zone(client, db, cp):
    _guest_cart(db)
    v = client.post("/api/v1/checkout/validate", json=_payload(cp), headers=HEADERS).json()
    assert v["is_valid"] is False and v["shipping_zone"] == "quote"
    assert v["errors"] == [QUOTE_ZONE_ERROR]
    r = client.post("/api/v1/checkout/create-payment-intent", json=_payload(cp), headers=HEADERS)
    assert r.status_code == 400 and r.json()["detail"] == QUOTE_ZONE_ERROR
    assert db.query(Order).count() == 0


def test_validate_baleares_charges_flat_rate(client, db):
    _guest_cart(db)
    v = client.post("/api/v1/checkout/validate", json=_payload("07001"), headers=HEADERS).json()
    assert v["is_valid"] and v["shipping_zone"] == "baleares"
    assert Decimal(v["shipping_cost"]) == Decimal("7.95")


def test_shipping_cost_with_postal_code(client, db):
    _guest_cart(db)
    r = client.get("/api/v1/checkout/shipping-cost", params={"postal_code": "07001"}, headers=HEADERS).json()
    assert Decimal(str(r["cost"])) == Decimal("7.95") and r["shipping_zone"] == "baleares"
    r = client.get("/api/v1/checkout/shipping-cost", headers=HEADERS).json()
    assert r["shipping_zone"] == "peninsula" and Decimal(str(r["cost"])) == Decimal("4.95")


def _pending_order(db):
    db.add(Order(order_number="CC-Z1", status="pending_payment", subtotal=Decimal("10"),
                 shipping_cost=Decimal("4.95"), discount=Decimal("0"), tax=Decimal("0"),
                 total=Decimal("14.95"), guest_email="ana@example.com", payment_intent_id="pi_z",
                 shipping_address={"first_name": "Ana"}))
    db.commit()


def test_pre_confirm_rejects_quote_zone_and_accepts_baleares(client, db, stripe_mock):
    PIModel.__table__.create(db.get_bind(), checkfirst=True)
    _pending_order(db)
    base = {"order_number": "CC-Z1", "payment_intent_id": "pi_z", "accept_terms": True}
    r = client.post("/api/v1/checkout/pre-confirm",
                    json=base | {"shipping_address": _payload("35001")["shipping_address"]})
    assert r.status_code == 400 and r.json()["detail"] == QUOTE_ZONE_ERROR
    r = client.post("/api/v1/checkout/pre-confirm",
                    json=base | {"shipping_address": _payload("07001")["shipping_address"]})
    assert r.status_code == 200


# ── pre-confirm recalcula el importe si cambia la zona ─────────────────────

from types import SimpleNamespace  # noqa: E402

import stripe as stripe_lib  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.models.payment import PaymentIntent as PIModel  # noqa: E402
from app.services import stripe_service  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


def _priced_order(db, shipping, total):
    PIModel.__table__.create(db.get_bind(), checkfirst=True)
    order = Order(order_number="CC-Z2", status="pending_payment", subtotal=Decimal("30"),
                  shipping_cost=Decimal(shipping), discount=Decimal("0"), tax=Decimal("0"),
                  total=Decimal(total), guest_email="ana@example.com", payment_intent_id="pi_z2",
                  shipping_address={"first_name": "Ana"})
    db.add(order)
    db.flush()
    db.add(PIModel(order_id=order.id, stripe_payment_intent_id="pi_z2", stripe_client_secret="s",
                   amount=int(Decimal(total) * 100)))
    db.commit()


@pytest.fixture()
def stripe_mock(monkeypatch):
    calls = []
    state = {"status": "requires_payment_method", "fail": False}
    monkeypatch.setattr(stripe_service, "retrieve_payment_intent",
                        lambda pi: SimpleNamespace(status=state["status"]))

    def modify(pi, amount):
        if state["fail"]:
            raise stripe_lib.StripeError("boom")
        calls.append((pi, amount))
    monkeypatch.setattr(stripe_service, "modify_payment_intent_amount", modify)
    return SimpleNamespace(calls=calls, state=state)


def _pre(client, cp):
    return client.post("/api/v1/checkout/pre-confirm", json={
        "order_number": "CC-Z2", "payment_intent_id": "pi_z2", "accept_terms": True,
        "shipping_address": _payload(cp)["shipping_address"]})


def test_pre_confirm_peninsula_to_baleares_raises_amount(client, db, stripe_mock):
    _priced_order(db, "4.95", "34.95")
    r = _pre(client, "07001")
    assert r.status_code == 200
    assert Decimal(str(r.json()["total"])) == Decimal("37.95")
    assert Decimal(str(r.json()["shipping_cost"])) == Decimal("7.95")
    assert stripe_mock.calls == [("pi_z2", 3795)]
    db.expire_all()
    order = db.query(Order).one()
    assert order.shipping_cost == Decimal("7.95") and order.total == Decimal("37.95")
    assert db.query(PIModel).one().amount == 3795


def test_pre_confirm_baleares_to_peninsula_lowers_amount(client, db, stripe_mock):
    _priced_order(db, "7.95", "37.95")
    assert _pre(client, "28001").status_code == 200
    assert stripe_mock.calls == [("pi_z2", 3495)]
    db.expire_all()
    assert db.query(Order).one().total == Decimal("34.95")


def test_pre_confirm_same_zone_does_not_touch_stripe(client, db, stripe_mock):
    _priced_order(db, "4.95", "34.95")
    assert _pre(client, "13005").status_code == 200
    assert stripe_mock.calls == []


def test_pre_confirm_stripe_failure_saves_nothing(client, db, stripe_mock):
    _priced_order(db, "4.95", "34.95")
    stripe_mock.state["fail"] = True
    r = _pre(client, "07001")
    assert r.status_code == 502
    assert r.json()["detail"] == "No hemos podido actualizar el importe del pedido. Inténtalo de nuevo."
    db.expire_all()
    order = db.query(Order).one()
    assert order.total == Decimal("34.95") and order.shipping_cost == Decimal("4.95")
    assert order.terms_accepted_at is None


def test_pre_confirm_pi_not_modifiable_returns_409(client, db, stripe_mock):
    _priced_order(db, "4.95", "34.95")
    stripe_mock.state["status"] = "processing"
    assert _pre(client, "07001").status_code == 409
    assert stripe_mock.calls == []
