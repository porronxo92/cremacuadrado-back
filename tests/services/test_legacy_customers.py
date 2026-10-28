"""Clientes de la web antigua: cupón personal del 20 % durante un año al verificar el email."""
import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.legacy_customer import LegacyCustomer
from app.models.order import Coupon, Order
from app.models.user import EmailVerificationToken, User
from app.services import email as email_mod
from app.services.coupon_redemptions import user_coupon_error
from app.services.legacy_customers import grant_legacy_benefit
from app.utils.security import create_access_token
from app.services.user_accounts import anonymize_user


@pytest.fixture()
def sent(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_send", lambda to, subject, html, *a, **k: sent.append((to, subject, html)) or True)
    return sent


@pytest.fixture()
def client(db, sent, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


def _register(client, email):
    r = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Clave12345!", "first_name": "Rubén", "last_name": "C",
        "accept_terms": True,
    })
    assert r.status_code == 201, r.text


def _user(db, email="ana@example.com", verified=True):
    u = User(email=email, password_hash="x", first_name="Ana", last_name="P", role="customer", email_verified=verified)
    db.add(u)
    db.commit()
    return u


def test_legacy_register_then_verify_grants_personal_coupon(db, client, sent):
    db.add(LegacyCustomer(email="ruben@example.com"))
    db.commit()

    _register(client, "Ruben@Example.com")
    subjects = [s for _, s, _ in sent]
    assert "Bienvenido a CremaCuadrado" not in subjects  # su bienvenida es la especial
    verification = next(h for _, s, h in sent if s.startswith("Verifica"))
    assert "20% de descuento" in verification
    assert db.query(Coupon).count() == 0  # sin verificar no hay cupón

    user = db.query(User).filter(User.email == "ruben@example.com").first()
    ev = db.query(EmailVerificationToken).filter(EmailVerificationToken.user_id == user.id).first()
    assert client.post(f"/api/v1/auth/verify-email?token={ev.token}").status_code == 200

    coupon = db.query(Coupon).one()
    assert coupon.code.startswith("CLIENTE20-")
    assert coupon.user_id == user.id and coupon.reusable and coupon.is_active
    assert coupon.discount_type == "percent" and coupon.discount_value == Decimal("20")
    # Periodo fijo de la promoción en hora peninsular: 28/10/2026 00:00 → 27/10/2027 23:59:59
    assert coupon.valid_from == datetime(2026, 10, 27, 23, 0)
    assert coupon.valid_until == datetime(2027, 10, 27, 21, 59, 59)
    special = [(to, h) for to, s, h in sent if s.startswith("Tu 20%")]
    assert len(special) == 1
    assert special[0][0] == "ruben@example.com" and coupon.code in special[0][1]
    assert "del 28/10/2026 al 27/10/2027" in special[0][1]

    # Logins posteriores no crean otro cupón ni reenvían el email
    grant_legacy_benefit(db, user)
    assert db.query(Coupon).count() == 1
    assert len([s for _, s, _ in sent if s.startswith("Tu 20%")]) == 1


def test_regular_register_keeps_normal_welcome(db, client, sent):
    _register(client, "otra@example.com")
    subjects = [s for _, s, _ in sent]
    assert "Bienvenido a CremaCuadrado" in subjects
    verification = next(h for _, s, h in sent if s.startswith("Verifica"))
    assert "20% de descuento" not in verification


def test_unverified_account_gets_nothing(db, sent):
    db.add(LegacyCustomer(email="ana@example.com"))
    user = _user(db, verified=False)
    grant_legacy_benefit(db, user)
    assert db.query(Coupon).count() == 0
    assert sent == []


def test_existing_verified_account_gets_benefit_on_login(db, client, sent, monkeypatch):
    from app.api.v1 import auth as auth_api
    monkeypatch.setattr(auth_api, "verify_password", lambda *a: True)
    _user(db)
    db.add(LegacyCustomer(email="ana@example.com"))
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": "Clave12345!"})
    assert r.status_code == 200, r.text
    assert db.query(Coupon).filter(Coupon.user_id.isnot(None)).count() == 1
    assert any(s.startswith("Tu 20%") for _, s, _ in sent)


def _paid_order(db, user, code):
    db.add(Order(
        order_number=f"CC-{code}", status="paid", user_id=user.id, coupon_code=code,
        subtotal=Decimal("10"), shipping_cost=Decimal("0"), discount=Decimal("2"), tax=Decimal("0"),
        total=Decimal("8"), shipping_address_json=json.dumps({"first_name": "Ana"}),
    ))
    db.commit()


def test_personal_coupon_rules(db):
    owner = _user(db)
    other = _user(db, email="otro@example.com")
    personal = Coupon(code="CLIENTE20-ABC234", discount_type="percent", discount_value=Decimal("20"),
                      user_id=owner.id, reusable=True)
    single = Coupon(code="DIEZ", discount_type="percent", discount_value=Decimal("10"))
    db.add_all([personal, single])
    db.commit()

    assert user_coupon_error(db, personal, other.id) == "Este cupón es personal y no está asociado a tu cuenta"
    _paid_order(db, owner, personal.code)
    assert user_coupon_error(db, personal, owner.id) is None  # reutilizable
    _paid_order(db, owner, single.code)
    assert user_coupon_error(db, single, owner.id) == "Este cupón ya fue utilizado en un pedido anterior"


def test_anonymize_disables_coupon_and_forgets_legacy_email(db, sent):
    db.add(LegacyCustomer(email="ana@example.com"))
    user = _user(db)
    grant_legacy_benefit(db, user)
    anonymize_user(db, user)
    db.commit()
    assert db.query(Coupon).one().is_active is False
    assert db.query(LegacyCustomer).count() == 0


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.token_version or 0)}"}


def test_profile_shows_only_own_legacy_coupon(db, client):
    db.add(LegacyCustomer(email="ana@example.com"))
    ana = _user(db)
    otro = _user(db, email="otro@example.com")
    grant_legacy_benefit(db, ana)
    r = client.get("/api/v1/users/me/legacy-coupon", headers=_auth(ana))
    assert r.status_code == 200 and r.json()["code"].startswith("CLIENTE20-")
    assert r.json()["valid_from"] == "2026-10-28" and r.json()["valid_until"] == "2027-10-27"
    r = client.get("/api/v1/users/me/legacy-coupon", headers=_auth(otro))
    assert r.status_code == 200 and r.json() is None


def test_admin_changes_period_for_granted_coupons(db, client):
    db.add(LegacyCustomer(email="ana@example.com"))
    ana = _user(db)
    grant_legacy_benefit(db, ana)
    admin = User(email="admin@example.com", password_hash="x", first_name="A", last_name="D",
                 role="admin", email_verified=True)
    db.add(admin)
    db.commit()

    assert client.get("/api/v1/admin/legacy-program", headers=_auth(ana)).status_code == 403
    r = client.get("/api/v1/admin/legacy-program", headers=_auth(admin))
    assert r.json() == {"starts_on": "2026-10-28", "ends_on": "2027-10-27", "customers": 1, "granted": 1}

    r = client.put("/api/v1/admin/legacy-program", headers=_auth(admin),
                   json={"starts_on": "2026-11-01", "ends_on": "2027-12-31"})
    assert r.status_code == 200, r.text
    coupon = db.query(Coupon).one()
    db.refresh(coupon)
    assert coupon.valid_from == datetime(2026, 10, 31, 23, 0)
    assert coupon.valid_until == datetime(2027, 12, 31, 22, 59, 59)

    r = client.put("/api/v1/admin/legacy-program", headers=_auth(admin),
                   json={"starts_on": "2027-01-01", "ends_on": "2026-01-01"})
    assert r.status_code == 422


def test_verification_token_expiry_accepts_aware_datetimes():
    """En PostgreSQL expires_at es TIMESTAMP WITH TIME ZONE: comparar no debe lanzar TypeError."""
    from datetime import timedelta, timezone
    future = EmailVerificationToken(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    past = EmailVerificationToken(expires_at=datetime.utcnow() - timedelta(hours=1))
    assert future.is_expired is False and past.is_expired is True
