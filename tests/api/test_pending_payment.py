"""Recordatorio y cancelación de pedidos pending_payment; endpoint resume-payment."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.cart import Cart, CartItem
from app.models.database import get_db
from app.models.order import Order, OrderItem
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.services import order_cleanup, stripe_service
from app.utils.security import create_access_token, get_password_hash


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
def sent(monkeypatch):
    """Emails de recordatorio enviados (mock)."""
    calls = []
    monkeypatch.setattr(order_cleanup.EmailService, "send_pending_payment_reminder",
                        lambda **kw: calls.append(kw) or True)
    return calls


@pytest.fixture()
def stripe_cancelled(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe_service, "cancel_payment_intent_best_effort",
                        lambda pi: calls.append(pi) or True)
    return calls


def _user(db, email="a@example.com"):
    u = User(email=email, first_name="Ana", last_name="L", password_hash=get_password_hash("x"))
    db.add(u)
    db.commit()
    return u


def _auth(u):
    return {"Authorization": f"Bearer {create_access_token(u.id)}"}


def _variant(db, stock=10, active=True, slug="p"):
    p = Product(slug=slug, name="Crema Pura")
    db.add(p)
    db.flush()
    v = ProductVariant(product_id=p.id, format="200g", weight_grams=200,
                       price=Decimal("9.50"), stock=stock, is_active=active)
    db.add(v)
    db.commit()
    return p, v


def _order(db, number, hours_old, user=None, reminder_hours_ago=None, status="pending_payment",
           lines=(), coupon=None, pi=None):
    o = Order(order_number=number, status=status, user_id=user.id if user else None,
              subtotal=Decimal("19"), shipping_cost=Decimal("0"), discount=Decimal("0"),
              tax=Decimal("0"), total=Decimal("19"), shipping_address_json=json.dumps({}),
              coupon_code=coupon, payment_intent_id=pi,
              created_at=datetime.utcnow() - timedelta(hours=hours_old),
              payment_reminder_sent_at=(datetime.utcnow() - timedelta(hours=reminder_hours_ago)
                                        if reminder_hours_ago is not None else None))
    db.add(o)
    db.flush()
    for p, v, qty in lines:
        db.add(OrderItem(order_id=o.id, product_id=p.id, product_variant_id=v.id,
                         product_name="Crema Pura 200g", quantity=qty,
                         unit_price=Decimal("9.50"), total=Decimal("9.50") * qty))
    db.commit()
    return o


# ── Recordatorio ────────────────────────────────────────────────────────────

def test_reminder_only_registered_past_threshold_and_once(db, sent):
    u = _user(db)
    p, v = _variant(db)
    _order(db, "CC-1", 25, user=u, lines=[(p, v, 2)])
    _order(db, "CC-NEW", 2, user=u)
    _order(db, "CC-GUEST", 30)
    _order(db, "CC-PAID", 30, user=u, status="paid")
    assert order_cleanup.send_pending_payment_reminders(db) == 1
    assert len(sent) == 1 and sent[0]["order_number"] == "CC-1" and sent[0]["to_email"] == "a@example.com"
    assert db.query(Order).filter_by(order_number="CC-1").one().payment_reminder_sent_at is not None
    # no se repite
    assert order_cleanup.send_pending_payment_reminders(db) == 0
    assert len(sent) == 1


def test_reminder_marked_even_if_send_fails(db, monkeypatch):
    u = _user(db)
    _order(db, "CC-1", 25, user=u)

    def boom(**kw):
        raise RuntimeError("smtp")
    monkeypatch.setattr(order_cleanup.EmailService, "send_pending_payment_reminder", boom)
    assert order_cleanup.send_pending_payment_reminders(db) == 0
    assert db.query(Order).one().payment_reminder_sent_at is not None


def test_reminder_email_escapes_and_links(monkeypatch):
    from app.services import email as email_mod
    captured = {}
    monkeypatch.setattr(
        email_mod, "_send",
        lambda to, subj, html, text="", mailbox="info": captured.update(html=html, text=text) or True,
    )
    email_mod.EmailService.send_pending_payment_reminder(
        "a@example.com", "<b>Ana</b>", "CC-1",
        [{"name": "<i>x</i>", "qty": 1, "total": Decimal("9.5")}], Decimal("9.5"))
    assert "<b>Ana</b>" not in captured["html"] and "&lt;b&gt;Ana" in captured["html"]
    assert "/account/orders?pedido=CC-1" in captured["html"]
    assert "Completar mi pedido" in captured["html"]


# ── Cancelación ─────────────────────────────────────────────────────────────

def test_cancel_rules(db, stripe_cancelled):
    u = _user(db)
    _order(db, "CC-GUEST-OLD", 49)
    _order(db, "CC-GUEST-NEW", 40)
    _order(db, "CC-USER-NOREM", 60, user=u)                          # sin recordatorio: no
    _order(db, "CC-USER-REM-5H", 60, user=u, reminder_hours_ago=5)   # recordatorio reciente: no
    _order(db, "CC-USER-OK", 60, user=u, reminder_hours_ago=13, pi="pi_1")
    _order(db, "CC-USER-YOUNG", 30, user=u, reminder_hours_ago=13)   # < 48 h: no
    _order(db, "CC-PAID", 99, user=u, status="paid")
    assert order_cleanup.cancel_ghost_orders(db) == 2
    st = {o.order_number: o.status for o in db.query(Order).all()}
    assert st["CC-GUEST-OLD"] == "cancelled" and st["CC-USER-OK"] == "cancelled"
    assert st["CC-GUEST-NEW"] == st["CC-USER-NOREM"] == st["CC-USER-REM-5H"] == st["CC-USER-YOUNG"] == "pending_payment"
    assert st["CC-PAID"] == "paid"
    assert stripe_cancelled == ["pi_1"]


def test_job_sends_reminder_before_cancelling(db, sent):
    u = _user(db)
    _order(db, "CC-1", 60, user=u)
    r = order_cleanup.run_pending_order_jobs(db)
    assert r == {"reminders_sent": 1, "cancelled": 0}  # aún no han pasado 12 h del aviso


# ── resume-payment ──────────────────────────────────────────────────────────

def test_resume_payment_moves_items_to_cart(client, db, stripe_cancelled):
    u = _user(db)
    p, v = _variant(db, stock=3)
    old_p, old_v = _variant(db, slug="otro")
    cart = Cart(user_id=u.id)
    db.add(cart)
    db.flush()
    db.add(CartItem(cart_id=cart.id, product_id=old_p.id, product_variant_id=old_v.id,
                    quantity=1, price_at_add=Decimal("9.50")))
    _order(db, "CC-1", 2, user=u, lines=[(p, v, 5)], coupon="BIENVENIDO10", pi="pi_9")
    r = client.post("/api/v1/orders/CC-1/resume-payment", headers=_auth(u))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order_number"] == "CC-1" and body["items_added"] == 1 and body["skipped"] == []
    assert body["message"]
    db.expire_all()
    cart = db.query(Cart).filter_by(user_id=u.id).one()
    assert [(i.product_variant_id, i.quantity) for i in cart.items] == [(v.id, 3)]  # limitado al stock
    assert cart.coupon_code == "BIENVENIDO10"
    assert db.query(Order).one().status == "cancelled"
    assert stripe_cancelled == ["pi_9"]


def test_resume_payment_not_owner_404(client, db):
    u, other = _user(db), _user(db, "b@example.com")
    p, v = _variant(db)
    _order(db, "CC-1", 2, user=u, lines=[(p, v, 1)])
    assert client.post("/api/v1/orders/CC-1/resume-payment", headers=_auth(other)).status_code == 404
    assert client.post("/api/v1/orders/CC-1/resume-payment").status_code in (401, 403)


def test_resume_payment_not_pending_409(client, db):
    u = _user(db)
    p, v = _variant(db)
    _order(db, "CC-1", 2, user=u, lines=[(p, v, 1)], status="paid")
    assert client.post("/api/v1/orders/CC-1/resume-payment", headers=_auth(u)).status_code == 409


def test_resume_payment_skips_out_of_stock(client, db, stripe_cancelled):
    u = _user(db)
    p, v = _variant(db, stock=5)
    p2, v2 = _variant(db, stock=0, slug="agotado")
    _order(db, "CC-1", 2, user=u, lines=[(p, v, 1), (p2, v2, 1)])
    r = client.post("/api/v1/orders/CC-1/resume-payment", headers=_auth(u))
    assert r.status_code == 200
    assert r.json()["items_added"] == 1 and r.json()["skipped"] == ["Crema Pura 200g"]


def test_resume_payment_nothing_addable_keeps_order(client, db, stripe_cancelled):
    u = _user(db)
    p, v = _variant(db, stock=0)
    cart = Cart(user_id=u.id)
    db.add(cart)
    db.flush()
    other_p, other_v = _variant(db, slug="otro")
    db.add(CartItem(cart_id=cart.id, product_id=other_p.id, product_variant_id=other_v.id,
                    quantity=2, price_at_add=Decimal("9.50")))
    _order(db, "CC-1", 2, user=u, lines=[(p, v, 1)], pi="pi_1")
    r = client.post("/api/v1/orders/CC-1/resume-payment", headers=_auth(u))
    assert r.status_code == 409 and r.json()["detail"]
    db.expire_all()
    assert db.query(Order).one().status == "pending_payment"
    assert [i.quantity for i in db.query(Cart).one().items] == [2]  # la cesta no se toca
    assert stripe_cancelled == []
