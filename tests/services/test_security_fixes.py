"""Correcciones de la revisión de seguridad (cupones, precios, webhook, tokens, emails)."""
import asyncio
import json
from datetime import datetime
from decimal import Decimal

from app.api.deps import get_current_user_optional
from app.api.v1 import webhooks
from app.api.v1.checkout import validate_checkout
from app.models.cart import Cart, CartItem
from app.models.order import Coupon, Order
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.schemas.order import AddressInput, CheckoutCreate
from app.utils.security import create_access_token, create_refresh_token


def _address():
    return AddressInput(first_name="Ana", last_name="Pérez", street="Calle Mayor 1", city="Toledo",
                        province="Toledo", postal_code="45001", phone="600123123")


def _user_with_cart(db, price_at_add=Decimal("9.95"), price=Decimal("9.95"), qty=2):
    user = User(email="u@example.com", first_name="U", last_name="S", password_hash="x")
    product = Product(slug="p", name="P")
    db.add_all([user, product])
    db.flush()
    variant = ProductVariant(product_id=product.id, format="200g", weight_grams=200, price=price, stock=50)
    db.add(variant)
    db.flush()
    cart = Cart(user_id=user.id)
    db.add(cart)
    db.flush()
    db.add(CartItem(cart_id=cart.id, product_id=product.id, product_variant_id=variant.id,
                    quantity=qty, price_at_add=price_at_add))
    db.commit()
    return user, cart


def _validate(db, user, **kw):
    data = CheckoutCreate(shipping_address=_address(), **kw)
    return asyncio.run(validate_checkout(data, db, user, None, None))


# ── Cupones ─────────────────────────────────────────────────────────────────

def test_checkout_rejects_coupon_already_used_by_customer(db):
    user, _ = _user_with_cart(db)
    db.add(Coupon(code="BIENVENIDO10", discount_type="percent", discount_value=Decimal("10")))
    db.add(Order(order_number="CC-OLD", user_id=user.id, status="delivered", coupon_code="BIENVENIDO10",
                 subtotal=Decimal("10"), shipping_cost=Decimal("0"), discount=Decimal("1"),
                 tax=Decimal("0"), total=Decimal("9"), shipping_address_json=json.dumps({})))
    db.commit()

    result = _validate(db, user, coupon_code="bienvenido10")

    assert not result.is_valid
    assert result.discount == 0
    assert "Este cupón ya fue utilizado en un pedido anterior" in result.errors


def test_percent_discount_is_rounded_to_cents():
    coupon = Coupon(code="X", discount_type="percent", discount_value=Decimal("10"), min_order_amount=Decimal("0"))
    assert coupon.calculate_discount(Decimal("13.45")) == Decimal("1.35")


# ── Precio vigente ──────────────────────────────────────────────────────────

def test_checkout_charges_current_price_not_price_at_add(db):
    user, cart = _user_with_cart(db, price_at_add=Decimal("5.00"), price=Decimal("9.95"), qty=2)

    result = _validate(db, user)

    assert result.subtotal == Decimal("19.90")


# ── Webhook idempotente ─────────────────────────────────────────────────────

def test_payment_webhook_ignores_orders_already_processed(db, monkeypatch):
    order = Order(order_number="CC-SHIP", status="shipped", subtotal=Decimal("10"), shipping_cost=Decimal("0"),
                  discount=Decimal("0"), tax=Decimal("0"), total=Decimal("10"),
                  shipping_address_json=json.dumps({}), paid_at=datetime(2026, 1, 1))
    db.add(order)
    db.commit()
    monkeypatch.setattr(webhooks, "_get_order_by_pi", lambda _db, _pi: order)

    webhooks._handle_payment_succeeded(db, {"id": "pi_x"})

    db.refresh(order)
    assert order.status == "shipped"
    assert order.paid_at == datetime(2026, 1, 1)


# ── Tokens ──────────────────────────────────────────────────────────────────

class _Creds:
    def __init__(self, token):
        self.credentials = token


def test_refresh_token_is_not_accepted_as_access_token(db):
    user = User(email="t@example.com", first_name="T", last_name="T", password_hash="x")
    db.add(user)
    db.commit()

    assert get_current_user_optional(db, _Creds(create_access_token(user.id))).id == user.id
    assert get_current_user_optional(db, _Creds(create_refresh_token(user.id))) is None


# ── Emails: los datos del usuario no se interpretan como HTML ───────────────

def test_contact_confirmation_escapes_user_name(monkeypatch):
    from app.services import email as email_mod
    sent = []
    monkeypatch.setattr(email_mod, "_send", lambda to, subject, html, *a, **k: sent.append(html) or True)

    email_mod.EmailService.send_contact_form_confirmation("v@example.com", '<a href="https://evil.test">Premio</a>')

    assert '<a href="https://evil.test">' not in sent[0]
    assert "&lt;a href=" in sent[0]
