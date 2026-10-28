"""Tests de API (TestClient + SQLite) de los flujos de cumplimiento."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.compliance import ConsentRecord, WithdrawalRequest
from app.models.database import get_db
from app.models.lead import NewsletterLead
from app.models.order import Order


@pytest.fixture()
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    sent = []
    from app.services import email as email_mod
    monkeypatch.setattr(email_mod, "_send", lambda *a, **k: sent.append(a) or True)
    with TestClient(app) as c:
        c.sent = sent
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


def make_order(db, **kw):
    order = Order(
        order_number=kw.get("number", "CC-261005-TEST01"), status=kw.get("status", "delivered"),
        subtotal=Decimal("10"), shipping_cost=Decimal("4.95"), discount=Decimal("0"), tax=Decimal("0"),
        total=Decimal("14.95"), guest_email="cliente@example.com", payment_intent_id="pi_123",
        delivered_at=kw.get("delivered_at", datetime.utcnow() - timedelta(days=3)),
        shipping_address_json=json.dumps({"first_name": "Ana", "last_name": "Pérez"}),
    )
    db.add(order)
    db.commit()
    return order


# ── Newsletter ───────────────────────────────────────────────────────────────

def test_newsletter_requires_consent(client):
    r = client.post("/api/v1/newsletter/subscribe", json={"email": "ana@example.com"})
    assert r.status_code == 400


def test_newsletter_double_opt_in_flow(client, db):
    r = client.post("/api/v1/newsletter/subscribe", json={"email": "ana@example.com", "consent": True})
    assert r.status_code == 201
    lead = db.query(NewsletterLead).one()
    assert lead.consent_at and lead.confirm_token and lead.confirmed_at is None
    assert db.query(ConsentRecord).filter_by(purpose="newsletter", granted=True).count() == 1

    # Mismo mensaje si se repite (no revela si el email existe)
    again = client.post("/api/v1/newsletter/subscribe", json={"email": "ana@example.com", "consent": True})
    assert again.status_code == 201 and again.json() == r.json()

    token = db.query(NewsletterLead).one().confirm_token
    assert client.post("/api/v1/newsletter/confirm", json={"token": token}).status_code == 200
    lead = db.query(NewsletterLead).one()
    assert lead.confirmed_at and lead.coupon_code

    assert client.post("/api/v1/newsletter/unsubscribe", json={"token": lead.unsubscribe_token}).status_code == 200
    assert db.query(NewsletterLead).one().unsubscribed_at is not None
    assert db.query(ConsentRecord).filter_by(purpose="newsletter", granted=False).count() == 1


# ── Desistimiento ────────────────────────────────────────────────────────────

def test_withdrawal_requires_matching_email(client, db):
    make_order(db)
    r = client.post("/api/v1/withdrawals", json={
        "order_number": "CC-261005-TEST01", "email": "otro@example.com", "full_name": "Ana Pérez"})
    assert r.status_code == 404


def test_withdrawal_is_registered_with_ack(client, db):
    make_order(db)
    r = client.post("/api/v1/withdrawals", json={
        "order_number": "cc-261005-test01", "email": "cliente@example.com", "full_name": "Ana Pérez"})
    assert r.status_code == 201
    body = r.json()
    assert body["reference"].startswith("D-") and body["within_term"] is True
    w = db.query(WithdrawalRequest).one()
    assert w.ack_sent_at is not None


def test_withdrawal_out_of_term_is_flagged(client, db):
    make_order(db, delivered_at=datetime.utcnow() - timedelta(days=20))
    r = client.post("/api/v1/withdrawals", json={
        "order_number": "CC-261005-TEST01", "email": "cliente@example.com", "full_name": "Ana Pérez"})
    assert r.status_code == 201 and r.json()["within_term"] is False


# ── Checkout: aceptación de condiciones ─────────────────────────────────────

def test_pre_confirm_requires_terms(client, db):
    make_order(db, status="pending_payment")
    payload = {"order_number": "CC-261005-TEST01", "payment_intent_id": "pi_123"}
    assert client.post("/api/v1/checkout/pre-confirm", json=payload).status_code == 400

    r = client.post("/api/v1/checkout/pre-confirm", json=payload | {"accept_terms": True, "terms_version": settings.TERMS_VERSION})
    assert r.status_code == 200
    order = db.query(Order).one()
    assert order.terms_version == settings.TERMS_VERSION and order.terms_accepted_at is not None


def test_pre_confirm_rejects_wrong_payment_intent(client, db):
    make_order(db, status="pending_payment")
    r = client.post("/api/v1/checkout/pre-confirm", json={
        "order_number": "CC-261005-TEST01", "payment_intent_id": "pi_other", "accept_terms": True})
    assert r.status_code == 404


# ── Cron ─────────────────────────────────────────────────────────────────────

def test_purge_endpoint_requires_cron_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "CRON_SECRET", "s3cret")
    assert client.get("/api/v1/maintenance/purge").status_code == 401
    assert client.get("/api/v1/maintenance/purge", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/api/v1/maintenance/purge", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and "deleted" in r.json()


def test_api_responses_carry_csp(client):
    r = client.get("/health")
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
