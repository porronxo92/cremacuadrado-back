"""Correcciones de las pruebas en STG (IVA, carrito, provincias, teléfono, enlaces)."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.v1.cart import get_or_create_cart
from app.models.cart import Cart, CartItem
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.schemas.order import AddressInput
from app.services.invoice import _pct
from app.utils.signing import invoice_token, verify_invoice_token
from app.utils.site import allowed_origin, site_url, use_site_url
from app.utils.spain import normalize_phone


def test_vat_percentage_is_not_truncated():
    assert _pct(Decimal("0.1000")) == "10 %"
    assert _pct(Decimal("0.21")) == "21 %"
    assert _pct(Decimal("0.055")) == "5,5 %"


# ── Carrito: un carrito de usuario antiguo no se recupera ───────────────────

def _user_cart_with_item(db, age_hours: int):
    user = User(email="u@example.com", first_name="U", last_name="S", password_hash="x")
    product = Product(slug="p", name="P")
    db.add_all([user, product])
    db.flush()
    variant = ProductVariant(product_id=product.id, format="200g", weight_grams=200, price=Decimal("9.95"), stock=50)
    db.add(variant)
    db.flush()
    cart = Cart(user_id=user.id)
    db.add(cart)
    db.flush()
    when = datetime.utcnow() - timedelta(hours=age_hours)
    db.add(CartItem(cart_id=cart.id, product_id=product.id, product_variant_id=variant.id, quantity=4,
                    price_at_add=Decimal("9.95"), created_at=when, updated_at=when))
    cart.updated_at = when
    db.commit()
    return user, cart


def test_recent_user_cart_is_kept(db):
    user, cart = _user_cart_with_item(db, age_hours=2)
    resolved = get_or_create_cart(db, user, None)
    assert resolved.id == cart.id
    assert db.query(CartItem).filter_by(cart_id=cart.id).count() == 1


def test_stale_user_cart_is_discarded(db):
    user, cart = _user_cart_with_item(db, age_hours=72)
    resolved = get_or_create_cart(db, user, None)
    assert resolved.id == cart.id
    assert db.query(CartItem).filter_by(cart_id=cart.id).count() == 0


# ── Provincias y teléfono ───────────────────────────────────────────────────

def _address(**kw):
    data = dict(first_name="Ana", last_name="Pérez", street="Calle Mayor 1", city="Ciudad Real",
                province="Ciudad Real", postal_code="13005", phone="600123456")
    data.update(kw)
    return AddressInput(**data)


def test_address_normalizes_province_and_phone():
    a = _address(province="ciudad real", phone="600 123 456")
    assert a.province == "Ciudad Real"
    assert a.phone == "+34 600123456"


def test_address_rejects_free_text_province():
    with pytest.raises(ValidationError):
        _address(province="Comunidad de Madrid", postal_code="28001")


def test_address_rejects_postcode_of_other_province():
    with pytest.raises(ValidationError):
        _address(province="Madrid", postal_code="13005")


def test_phone_with_foreign_prefix():
    assert normalize_phone("+351 912345678") == "+351 912345678"
    with pytest.raises(ValueError):
        normalize_phone("+34 512345678")


# ── Enlaces ─────────────────────────────────────────────────────────────────

def test_invoice_token_roundtrip():
    token = invoice_token("CC-261005-ABC123")
    assert verify_invoice_token("CC-261005-ABC123", token)
    assert not verify_invoice_token("CC-261005-OTHER1", token)


def test_site_url_uses_only_allowed_origins(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://staging.cremacuadrado.com"])
    monkeypatch.setattr(settings, "SITE_URL", "https://cremacuadrado-front.vercel.app")
    assert allowed_origin("https://evil.example") is None
    with use_site_url("https://staging.cremacuadrado.com"):
        assert site_url() == "https://staging.cremacuadrado.com"
    with use_site_url("https://evil.example"):
        assert site_url() == "https://cremacuadrado-front.vercel.app"
