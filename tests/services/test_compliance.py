"""Tests de cumplimiento: Omnibus, desistimiento, purga, anonimización y logs."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.api.v1.withdrawals import _within_term
from app.logging_config import mask_emails
from app.models.cart import Cart
from app.models.compliance import ConsentRecord, PriceHistory
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.order import Order
from app.models.product import Product, ProductVariant
from app.models.user import Address, User
from app.services import consents
from app.services.data_export import export_user_data
from app.services.price_history import prior_lowest_price
from app.services.retention import purge
from app.services.user_accounts import anonymize_user


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_variant(db, price="9.95"):
    product = Product(slug="crema-pura", name="Crema Pura")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, format="200g", weight_grams=200, price=Decimal(price))
    db.add(variant)
    db.flush()
    return variant


def backdate_history(db, variant, days):
    for row in db.query(PriceHistory).filter(PriceHistory.variant_id == variant.id):
        row.valid_from -= timedelta(days=days)
        if row.valid_to:
            row.valid_to -= timedelta(days=days)
    db.flush()


# ── Omnibus ──────────────────────────────────────────────────────────────────

def test_new_variant_opens_price_history(db):
    variant = make_variant(db)
    rows = db.query(PriceHistory).filter(PriceHistory.variant_id == variant.id).all()
    assert len(rows) == 1 and rows[0].valid_to is None and rows[0].price == Decimal("9.95")


def test_price_change_closes_and_opens_period(db):
    variant = make_variant(db)
    variant.price = Decimal("8.95")
    db.flush()
    rows = db.query(PriceHistory).filter(PriceHistory.variant_id == variant.id).order_by(PriceHistory.id).all()
    assert len(rows) == 2
    assert rows[0].valid_to is not None and rows[1].valid_to is None
    assert rows[1].price == Decimal("8.95")


def test_reduction_shows_lowest_price_of_previous_30_days(db):
    variant = make_variant(db, "9.95")
    backdate_history(db, variant, 40)            # 9,95 desde hace 40 días
    variant.price = Decimal("8.50")
    db.flush()
    backdate_history(db, variant, 20)            # 8,50 durante 20 días
    variant.price = Decimal("9.95")
    db.flush()
    backdate_history(db, variant, 5)             # vuelve a 9,95 hace 5 días
    variant.price = Decimal("7.95")              # rebaja hoy
    db.flush()
    # El más bajo de los 30 días previos es 8,50 (no 9,95)
    assert prior_lowest_price(db, variant) == Decimal("8.50")


def test_no_prior_price_when_not_a_reduction(db):
    variant = make_variant(db, "9.95")
    assert prior_lowest_price(db, variant) is None          # sin historial previo
    backdate_history(db, variant, 10)
    variant.price = Decimal("10.95")                         # subida
    db.flush()
    assert prior_lowest_price(db, variant) is None


# ── Desistimiento ────────────────────────────────────────────────────────────

def test_withdrawal_term_is_14_days_from_delivery():
    now = _now()
    assert _within_term(Order(delivered_at=None), now)
    assert _within_term(Order(delivered_at=now - timedelta(days=13)), now)
    assert not _within_term(Order(delivered_at=now - timedelta(days=15)), now)


# ── Purga ────────────────────────────────────────────────────────────────────

def test_purge_removes_expired_data_and_keeps_active_subscribers(db):
    old = _now() - timedelta(days=400)
    db.add_all([
        Cart(session_id="guest-old", updated_at=old, created_at=old),
        Cart(session_id="guest-new"),
        NewsletterLead(email="pending@example.com", created_at=old),                       # sin confirmar
        NewsletterLead(email="active@example.com", created_at=old, confirmed_at=old),      # suscriptor
        ContactLead(name="A", email="a@example.com", message="hola hola hola", created_at=old),
        ContactLead(name="B", email="b@example.com", message="hola hola hola"),
    ])
    db.commit()

    deleted = purge(db)
    assert deleted["guest_carts"] == 1
    assert deleted["newsletter_unconfirmed"] == 1
    assert deleted["contact_leads"] == 1
    assert {n.email for n in db.query(NewsletterLead)} == {"active@example.com"}


# ── Supresión y exportación ──────────────────────────────────────────────────

def test_anonymize_removes_personal_data_but_keeps_consent_trail(db):
    user = User(email="ana@example.com", first_name="Ana", last_name="Pérez", phone="600", password_hash="x")
    db.add(user)
    db.flush()
    db.add(Address(user_id=user.id, first_name="Ana", last_name="Pérez", street="Mayor 1", city="Madrid",
                   province="Madrid", postal_code="28001", phone="600"))
    db.add(NewsletterLead(email="ana@example.com"))
    consents.record_consent(db, email="ana@example.com", purpose=consents.NEWSLETTER, granted=True, source="test")
    db.flush()

    anonymize_user(db, user)
    db.flush()

    assert user.email.endswith("@cremacuadrado.invalid") and user.phone is None and not user.is_active
    assert db.query(Address).count() == 0
    assert db.query(NewsletterLead).count() == 0
    consent = db.query(ConsentRecord).one()
    assert consent.user_id == user.id


def test_export_contains_account_and_consents(db):
    user = User(email="ana@example.com", first_name="Ana", last_name="Pérez", password_hash="x")
    db.add(user)
    db.flush()
    consents.record_consent(db, email="ana@example.com", purpose=consents.MARKETING, granted=True,
                            source="register", user_id=user.id)
    db.flush()
    data = export_user_data(db, user)
    json.dumps(data)  # serializable
    assert data["account"]["email"] == "ana@example.com"
    assert data["consents"][0]["purpose"] == "marketing"


# ── Logs ─────────────────────────────────────────────────────────────────────

def test_emails_are_masked_in_logs():
    assert mask_emails("Login failed: email=ana.perez@gmail.com") == "Login failed: email=a***@gmail.com"
