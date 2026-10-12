"""Pedidos de invitado: email de confirmación sin «Ver mis pedidos» y vinculación a la cuenta."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.order import Order
from app.models.user import EmailVerificationToken, User
from app.services import email as email_mod
from app.services.user_accounts import link_guest_orders
from app.utils.signing import invoice_token


def _email_data(is_guest, to="ana@example.com"):
    return email_mod.OrderEmailData(
        to_email=to, customer_name="Ana", order_number="CC-261005-GUEST1",
        order_date=datetime(2026, 10, 5, 10, 0), items=[{"name": "Crema Pura 200g", "qty": 1, "unit_price": 9.95, "total": 9.95}],
        subtotal=Decimal("9.95"), shipping_cost=Decimal("4.95"), discount=Decimal("0"), total=Decimal("14.90"),
        shipping_address={"first_name": "Ana", "street": "Calle 1", "postal_code": "13001", "city": "Ciudad Real"},
        is_guest=is_guest,
    )


@pytest.fixture()
def sent_html(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_send", lambda to, subject, html, *a, **k: sent.append(html) or True)
    return sent


def test_guest_confirmation_has_signed_invoice_link_and_no_my_orders(sent_html):
    email_mod.send_order_confirmation(_email_data(is_guest=True))
    html = sent_html[0]
    assert "Ver mis pedidos" not in html
    assert "/account/orders" not in html
    token = invoice_token("CC-261005-GUEST1")
    html = html.replace("&amp;", "&")  # el botón escapa los & del href
    assert f"/factura?pedido=CC-261005-GUEST1&token={token}" in html
    assert "Descargar mi factura" in html
    assert "/auth/register?email=ana%40example.com" in html


def test_registered_confirmation_unchanged(sent_html):
    email_mod.send_order_confirmation(_email_data(is_guest=False))
    html = sent_html[0]
    assert "Ver mis pedidos" in html
    assert "/account/orders" in html
    assert "Descargar mi factura" not in html
    assert "Crear mi cuenta" not in html


def test_status_update_email_hides_account_button_for_guests(sent_html):
    email_mod.EmailService.send_order_status_update_email("a@example.com", "CC-1", "Ana", "refunded", is_guest=True)
    email_mod.EmailService.send_order_status_update_email("a@example.com", "CC-1", "Ana", "refunded")
    assert "Ver mis pedidos" not in sent_html[0]
    assert "Ver mis pedidos" in sent_html[1]


# ── Vinculación ──────────────────────────────────────────────────────────────

def _guest_order(db, number, email="Ana@Example.com"):
    order = Order(
        order_number=number, status="paid", subtotal=Decimal("10"), shipping_cost=Decimal("4.95"),
        discount=Decimal("0"), tax=Decimal("0"), total=Decimal("14.95"), guest_email=email,
        shipping_address_json=json.dumps({"first_name": "Ana"}),
    )
    db.add(order)
    db.commit()
    return order


def _user(db, verified, email="ana@example.com"):
    u = User(email=email, password_hash="x", first_name="Ana", last_name="P", role="customer", email_verified=verified)
    db.add(u)
    db.commit()
    return u


def test_link_requires_verified_email(db):
    order = _guest_order(db, "CC-1")
    user = _user(db, verified=False)
    assert link_guest_orders(db, user) == 0
    db.commit()
    db.refresh(order)
    assert order.user_id is None


def test_link_when_verified_is_case_insensitive_and_idempotent(db):
    mine = _guest_order(db, "CC-1")
    other = _guest_order(db, "CC-2", email="otra@example.com")
    user = _user(db, verified=True)
    assert link_guest_orders(db, user) == 1
    db.commit()
    assert link_guest_orders(db, user) == 0
    db.refresh(mine); db.refresh(other)
    assert mine.user_id == user.id
    assert other.user_id is None


def test_link_does_not_steal_orders_of_another_user(db):
    owner = _user(db, verified=True, email="owner@example.com")
    order = _guest_order(db, "CC-3", email="ana@example.com")
    order.user_id = owner.id
    db.commit()
    user = _user(db, verified=True)
    assert link_guest_orders(db, user) == 0
    db.refresh(order)
    assert order.user_id == owner.id


@pytest.fixture()
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    monkeypatch.setattr(email_mod, "_send", lambda *a, **k: True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


def test_verify_email_links_guest_orders_register_does_not(db, client):
    order = _guest_order(db, "CC-4")
    r = client.post("/api/v1/auth/register", json={
        "email": "ana@example.com", "password": "Clave12345!", "first_name": "Ana", "last_name": "P",
        "accept_terms": True,
    })
    assert r.status_code == 201, r.text
    db.refresh(order)
    assert order.user_id is None  # sin verificar no se vincula

    user = db.query(User).filter(User.email == "ana@example.com").first()
    ev = db.query(EmailVerificationToken).filter(EmailVerificationToken.user_id == user.id).first()
    assert client.post(f"/api/v1/auth/verify-email?token={ev.token}").status_code == 200
    db.refresh(order)
    assert order.user_id == user.id


def test_link_also_updates_invoice_user_id_and_anonymize_clears_guest_email(db):
    from app.models.invoice import Invoice
    from app.services import invoicing
    from app.services.user_accounts import anonymize_user
    order = _guest_order(db, "CC-5")
    order.paid_at = datetime(2026, 10, 5, 10, 0)
    order.tax_rate = Decimal("0.10")
    db.commit()
    inv = invoicing.issue_invoice_for_order(db, order)
    db.commit()
    assert inv.user_id is None
    user = _user(db, verified=True)
    link_guest_orders(db, user)
    db.commit()
    db.refresh(inv)
    assert inv.user_id == user.id
    anonymize_user(db, user)
    db.commit()
    db.refresh(order)
    assert order.user_id == user.id and order.guest_email is None
