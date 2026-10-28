"""Totales de carrito/checkout con cupones (percent, fixed, free_shipping)."""
import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.cart import _load_cart, apply_coupon, cart_to_response
from app.api.v1.checkout import QUOTE_ZONE_ERROR, get_shipping_cost, validate_checkout
from app.models.cart import Cart, CartItem
from app.models.order import Coupon, Order
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.schemas.cart import ApplyCoupon
from app.schemas.coupon import CouponCreate, CouponUpdate
from app.schemas.order import AddressInput, CheckoutCreate
from app.utils.spain import PROVINCES
from app.services.coupon_redemptions import _redeemed_amount
from app.services.pricing import compute_totals, format_eur, shipping_zone


def _cart(db, price="10.00", qty=1):
    user = User(email="u@example.com", first_name="U", last_name="S", password_hash="x")
    product = Product(slug="p", name="P")
    db.add_all([user, product])
    db.flush()
    variant = ProductVariant(product_id=product.id, format="200g", weight_grams=200,
                             price=Decimal(price), stock=500)
    db.add(variant)
    db.flush()
    cart = Cart(user_id=user.id)
    db.add(cart)
    db.flush()
    item = CartItem(cart_id=cart.id, product_id=product.id, product_variant_id=variant.id,
                    quantity=qty, price_at_add=Decimal(price))
    db.add(item)
    db.commit()
    return user, cart, item


def _coupon(db, **kw):
    data = dict(code="ENVIOGRATIS", discount_type="free_shipping", discount_value=Decimal("0"),
                min_order_amount=Decimal("0"))
    data.update(kw)
    c = Coupon(**data)
    db.add(c)
    db.commit()
    return c


def _checkout():
    return CheckoutCreate(shipping_address=AddressInput(
        first_name="Ana", last_name="Pérez", street="Calle Mayor 1", city="Ciudad Real",
        province="Ciudad Real", postal_code="13005", phone="600123456"))


def test_format_eur():
    assert format_eur(Decimal("7.95")) == "7,95 €"
    assert format_eur(5) == "5,00 €"
    assert format_eur(Decimal("1234.5")) == "1.234,50 €"


def test_messages_without_coupon():
    t = compute_totals(Decimal("40.05"))
    assert t.shipping_message == "Te faltan 7,95 € para el envío gratis"
    assert t.shipping_cost == Decimal("4.95")
    assert compute_totals(Decimal("48")).shipping_message == "Envío gratis"


def test_free_shipping_below_threshold(db):
    user, cart, _ = _cart(db, "10.00", 2)
    _coupon(db)
    cart.coupon_code = "ENVIOGRATIS"
    db.commit()
    r = cart_to_response(_load_cart(db, cart.id), db)
    assert r.discount == 0
    assert r.shipping_discount == Decimal("4.95")
    assert r.shipping_cost == 0
    assert r.total == r.subtotal == Decimal("20.00")
    assert r.coupon.applies and r.coupon.discount_amount == Decimal("4.95")
    assert r.shipping_message == "Envío gratis con el cupón ENVIOGRATIS"


def test_apply_free_shipping_rejected_when_already_free(db):
    user, cart, _ = _cart(db, "24.00", 2)
    _coupon(db)
    with pytest.raises(HTTPException) as exc:
        apply_coupon(ApplyCoupon(code="enviogratis"), db, user, None, None)
    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Tu pedido ya tiene envío gratis (desde 48,00 €). Guarda el cupón ENVIOGRATIS para otro pedido."
    )
    assert db.query(Cart).get(cart.id).coupon_code is None


def test_apply_rejected_under_minimum(db):
    user, cart, _ = _cart(db, "10.00", 1)
    _coupon(db, code="MIN30", discount_type="fixed", discount_value=Decimal("5"),
            min_order_amount=Decimal("30"))
    with pytest.raises(HTTPException) as exc:
        apply_coupon(ApplyCoupon(code="MIN30"), db, user, None, None)
    assert exc.value.detail == "Añade 20,00 € más para usar el cupón MIN30 (pedido mínimo 30,00 €)."


def test_applied_coupon_stays_but_stops_applying_over_threshold(db):
    user, cart, item = _cart(db, "10.00", 2)
    _coupon(db)
    apply_coupon(ApplyCoupon(code="ENVIOGRATIS"), db, user, None, None)
    item.quantity = 5
    db.commit()
    r = cart_to_response(_load_cart(db, cart.id), db)
    assert r.coupon is not None and r.coupon.applies is False
    assert r.coupon.message == "Tu pedido ya tiene envío gratis, así que el cupón ENVIOGRATIS no se aplica."
    assert r.shipping_discount == 0 and r.shipping_cost == 0 and r.total == Decimal("50.00")
    assert db.query(Cart).get(cart.id).coupon_code == "ENVIOGRATIS"


def test_fixed_coupon_below_minimum_after_removing_items(db):
    user, cart, item = _cart(db, "10.00", 4)
    _coupon(db, code="MIN30", discount_type="fixed", discount_value=Decimal("5"),
            min_order_amount=Decimal("30"))
    apply_coupon(ApplyCoupon(code="MIN30"), db, user, None, None)
    assert cart_to_response(_load_cart(db, cart.id), db).discount == Decimal("5.00")
    item.quantity = 2
    db.commit()
    r = cart_to_response(_load_cart(db, cart.id), db)
    assert r.coupon.applies is False and r.discount == 0
    assert r.coupon.message == "Añade 10,00 € más para usar el cupón MIN30 (pedido mínimo 30,00 €)."
    assert r.total == Decimal("20.00") + Decimal("4.95")


def test_percent_coupon_still_uses_threshold_after_discount():
    class C:
        code = "P"
        discount_type = "percent"
        discount_value = Decimal("10")
        min_order_amount = Decimal("0")
        max_discount_amount = None
        calculate_discount = Coupon.calculate_discount
    t = compute_totals(Decimal("50"), C())
    assert t.discount == Decimal("5.00") and t.shipping_cost == Decimal("4.95")
    assert t.total == Decimal("49.95")


def test_cart_validate_and_shipping_cost_agree(db):
    user, cart, _ = _cart(db, "10.00", 2)
    _coupon(db)
    cart.coupon_code = "ENVIOGRATIS"
    db.commit()
    shown = cart_to_response(_load_cart(db, cart.id), db)
    v = asyncio.run(validate_checkout(_checkout(), db, user, None, None))
    assert v.is_valid
    assert v.total == shown.total  # lo que cobra el PaymentIntent
    assert v.shipping_cost == shown.shipping_cost == 0
    assert v.shipping_discount == shown.shipping_discount == Decimal("4.95")
    s = asyncio.run(get_shipping_cost(db, user, None, None))
    assert s.cost == 0 and s.message == "Envío gratis con el cupón ENVIOGRATIS"


def test_validate_non_applying_coupon_is_not_an_error(db):
    user, cart, _ = _cart(db, "30.00", 2)
    _coupon(db)
    cart.coupon_code = "ENVIOGRATIS"
    db.commit()
    v = asyncio.run(validate_checkout(_checkout(), db, user, None, None))
    assert v.is_valid and v.shipping_discount == 0 and v.total == Decimal("60.00")


def test_redemption_of_free_shipping_records_voided_shipping(db):
    user, cart, _ = _cart(db)
    coupon = _coupon(db)
    order = Order(order_number="CC-1", status="paid", user_id=user.id, subtotal=Decimal("20"),
                  shipping_cost=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"),
                  total=Decimal("20"), coupon_code="ENVIOGRATIS")
    assert _redeemed_amount(order, coupon) == Decimal("4.95")
    fixed = _coupon(db, code="F5", discount_type="fixed", discount_value=Decimal("5"))
    order.discount = Decimal("5")
    assert _redeemed_amount(order, fixed) == Decimal("5")


def test_coupon_schema_free_shipping_forces_value():
    c = CouponCreate(code="x", discount_type="free_shipping", discount_value=Decimal("3"),
                     max_discount_amount=Decimal("9"))
    assert c.discount_value == 0 and c.max_discount_amount is None
    assert CouponCreate(code="y", discount_type="free_shipping").discount_value == 0
    with pytest.raises(ValidationError):
        CouponCreate(code="z", discount_type="fixed", discount_value=Decimal("0"))
    with pytest.raises(ValidationError):
        CouponCreate(code="z", discount_type="percent", discount_value=Decimal("101"))
    assert CouponUpdate(is_active=False).discount_type is None


# ── Zonas de envío: península / Baleares / presupuesto ─────────────────────

def test_shipping_zone_by_postal_code():
    assert shipping_zone("13005") == "peninsula"
    assert shipping_zone(None) == "peninsula"
    assert shipping_zone("07001") == "baleares"
    assert shipping_zone(" 07720") == "baleares"
    for cp in ("35001", "38001", "51001", "52001"):
        assert shipping_zone(cp) == "quote"


def test_baleares_shipping_has_no_free_threshold():
    t30 = compute_totals(Decimal("30"), zone="baleares")
    t60 = compute_totals(Decimal("60"), zone="baleares")
    assert t30.shipping_cost == t60.shipping_cost == Decimal("7.95")
    assert t60.total == Decimal("67.95")
    assert t30.shipping_message == "Envío a Islas Baleares: 7,95 €"


def test_free_shipping_coupon_is_peninsula_only(db):
    coupon = _coupon(db)
    for zone in ("baleares", "quote"):
        t = compute_totals(Decimal("20"), coupon, zone=zone)
        assert not t.coupon_applies and t.shipping_discount == 0
        assert t.coupon_message == "El cupón ENVIOGRATIS solo es válido para envíos a la península."
    t = compute_totals(Decimal("20"), coupon, zone="baleares")
    assert t.shipping_cost == Decimal("7.95") and t.total == Decimal("27.95")
    assert t.shipping_message == "Envío a Islas Baleares: 7,95 €"


def test_validate_baleares_with_free_shipping_coupon_charges_shipping(db):
    user, cart, _ = _cart(db, "10.00", 2)
    _coupon(db)
    cart.coupon_code = "ENVIOGRATIS"
    db.commit()
    v = asyncio.run(validate_checkout(_checkout_cp("07001"), db, user, None, None))
    assert v.is_valid and v.shipping_zone == "baleares"
    assert v.shipping_discount == 0 and v.shipping_cost == Decimal("7.95")
    assert v.total == Decimal("27.95")


def test_quote_zone_charges_no_shipping():
    t = compute_totals(Decimal("30"), zone="quote")
    assert t.shipping_cost == 0 and t.total == Decimal("30")
    assert t.shipping_message == "Envío a Canarias, Ceuta y Melilla: presupuesto por WhatsApp"


def test_peninsula_unchanged():
    assert compute_totals(Decimal("30")).shipping_cost == Decimal("4.95")
    assert compute_totals(Decimal("48"), zone="peninsula").shipping_cost == 0


def _checkout_cp(cp):
    return CheckoutCreate(shipping_address=AddressInput(
        first_name="Ana", last_name="Pérez", street="Calle Mayor 1", city="X",
        province=PROVINCES[cp[:2]], postal_code=cp, phone="600123456"))


def test_validate_baleares_ok_and_quote_blocked(db):
    user, cart, _ = _cart(db, "30.00", 1)
    v = asyncio.run(validate_checkout(_checkout_cp("07001"), db, user, None, None))
    assert v.is_valid and v.shipping_zone == "baleares"
    assert v.shipping_cost == Decimal("7.95") and v.total == Decimal("37.95")
    for cp in ("35001", "38001", "51001", "52001"):
        v = asyncio.run(validate_checkout(_checkout_cp(cp), db, user, None, None))
        assert not v.is_valid and v.shipping_zone == "quote"
        assert v.errors == [QUOTE_ZONE_ERROR]
    assert "WhatsApp" in QUOTE_ZONE_ERROR


def test_shipping_cost_endpoint_by_postal_code(db):
    user, cart, _ = _cart(db, "30.00", 1)
    r = asyncio.run(get_shipping_cost(db, user, None, None, postal_code="07001"))
    assert r.cost == Decimal("7.95") and r.shipping_zone == "baleares"
    r = asyncio.run(get_shipping_cost(db, user, None, None))
    assert r.cost == Decimal("4.95") and r.shipping_zone == "peninsula"

