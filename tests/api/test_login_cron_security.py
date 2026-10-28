"""Bloqueo de login por (email, IP) y endpoints de Vercel Cron."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.order import Order
from app.models.user import LoginAttempt, User
from app.utils.security import get_password_hash


@pytest.fixture()
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.state.limiter.enabled = False
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.limiter.enabled = True


def _login(client, password, ip, email="owner@example.com"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password},
                       headers={"X-Forwarded-For": ip})


@pytest.fixture()
def owner(db):
    u = User(email="owner@example.com", first_name="O", last_name="W",
             password_hash=get_password_hash("Correcta123!"))
    db.add(u)
    db.commit()
    return u


def test_attacker_cannot_lock_out_owner_from_another_ip(client, owner):
    for _ in range(5):
        assert _login(client, "mala", "6.6.6.6").status_code == 401
    assert _login(client, "mala", "6.6.6.6").status_code == 429
    # el atacante bloqueado no puede ni con la contraseña correcta
    assert _login(client, "Correcta123!", "6.6.6.6").status_code == 429
    # el titular, desde su IP, entra con normalidad
    assert _login(client, "Correcta123!", "1.1.1.1").status_code == 200


def test_distributed_bruteforce_locks_account_for_unknown_ips_only(client, db, owner, monkeypatch):
    from app.services import login_throttle
    monkeypatch.setattr(login_throttle, "ACCOUNT_THRESHOLD", 6)
    assert _login(client, "Correcta123!", "1.1.1.1").status_code == 200  # IP conocida
    for i in range(6):
        assert _login(client, "mala", f"9.9.9.{i}").status_code == 401
    # IP nueva: bloqueada aunque acierte (freno a la fuerza bruta distribuida)
    assert _login(client, "Correcta123!", "8.8.8.8").status_code == 429
    # IP donde el titular ya entró: sigue pudiendo
    assert _login(client, "Correcta123!", "1.1.1.1").status_code == 200


def test_throttle_applies_to_unknown_emails_without_revealing_existence(client):
    codes = [_login(client, "x", "5.5.5.5", email="nadie@example.com").status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_inactive_account_not_revealed_without_password(client, db, owner):
    owner.is_active = False
    db.commit()
    assert _login(client, "mala", "1.1.1.1").status_code == 401
    assert _login(client, "Correcta123!", "1.1.1.1").status_code == 403


def test_success_clears_pair_counter(client, db, owner):
    for _ in range(4):
        _login(client, "mala", "1.1.1.1")
    assert _login(client, "Correcta123!", "1.1.1.1").status_code == 200
    assert db.get(LoginAttempt, ("owner@example.com", "1.1.1.1")).failures == 0


# ── Cron ────────────────────────────────────────────────────────────────────

def _ghost(db, number, status, minutes_old):
    o = Order(order_number=number, status=status, subtotal=Decimal("10"), shipping_cost=Decimal("0"),
              discount=Decimal("0"), tax=Decimal("0"), total=Decimal("10"),
              shipping_address_json=json.dumps({}),
              created_at=datetime.utcnow() - timedelta(minutes=minutes_old))
    db.add(o)
    return o


@pytest.mark.parametrize("path", ["purge", "ghost-orders", "poll-shipments"])
def test_cron_endpoints_require_secret(client, monkeypatch, path):
    monkeypatch.setattr(settings, "CRON_SECRET", "s3cret")
    assert client.get(f"/api/v1/maintenance/{path}").status_code == 401
    assert client.get(f"/api/v1/maintenance/{path}", headers={"Authorization": "Bearer mal"}).status_code == 401
    monkeypatch.setattr(settings, "CRON_SECRET", "")
    assert client.get(f"/api/v1/maintenance/{path}", headers={"Authorization": "Bearer "}).status_code == 401


def test_ghost_orders_cron_cancels_only_expired_pending(client, db, monkeypatch):
    monkeypatch.setattr(settings, "CRON_SECRET", "s3cret")
    _ghost(db, "CC-OLD", "pending_payment", settings.PENDING_ORDER_EXPIRE_HOURS * 60 + 5)
    _ghost(db, "CC-NEW", "pending_payment", 1)
    _ghost(db, "CC-PAID", "paid", 600)
    db.commit()
    r = client.get("/api/v1/maintenance/ghost-orders", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["cancelled"] == 1
    st = {o.order_number: o.status for o in db.query(Order).all()}
    assert st == {"CC-OLD": "cancelled", "CC-NEW": "pending_payment", "CC-PAID": "paid"}


def test_scheduler_not_started_on_vercel(monkeypatch):
    import asyncio
    from app import main
    monkeypatch.setenv("VERCEL", "1")
    started = []
    monkeypatch.setattr(main.logger, "warning", lambda *a, **k: started.append(a))

    async def run():
        async with main.lifespan(main.app):
            pass
    asyncio.run(run())
    assert started == []


# ── Abuso de formularios que envían emails ──────────────────────────────────

@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    from app.services import email as email_mod
    monkeypatch.setattr(email_mod, "_send", lambda *a, **k: sent.append(a) or True)
    return sent


def test_forgot_password_does_not_resend_within_cooldown(client, db, owner, outbox):
    for _ in range(3):
        assert client.post("/api/v1/auth/forgot-password", json={"email": owner.email}).status_code == 200
    assert len(outbox) == 1


def test_newsletter_subscribe_does_not_resend_confirmation(client, outbox):
    body = {"email": "victima@example.com", "consent": True}
    for _ in range(3):
        assert client.post("/api/v1/newsletter/subscribe", json=body).status_code == 201
    assert len(outbox) == 1


def test_contact_form_acks_sender_once_per_window(client, outbox):
    body = {"name": "Ana", "email": "victima@example.com", "message": "Mensaje de prueba largo",
            "accept_privacy": True}
    for _ in range(3):
        assert client.post("/api/v1/contact/submit", json=body).status_code == 201
    to_victim = [m for m in outbox if "victima@example.com" in str(m[0])]
    assert len(to_victim) == 1


def test_admin_panel_login_uses_pair_throttle(db, monkeypatch):
    import asyncio
    from starlette.requests import Request
    from app import sqladmin_config

    admin = User(email="admin@example.com", first_name="A", last_name="D", role="admin",
                 password_hash=get_password_hash("Admin12345!"))
    db.add(admin)
    db.commit()
    monkeypatch.setattr(sqladmin_config, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    from app.services import audit
    monkeypatch.setattr(audit, "write", lambda **k: None)

    class Req(Request):
        def __init__(self, ip, pw):
            super().__init__({"type": "http", "headers": [(b"x-forwarded-for", ip.encode())], "session": {}})
            self._f = {"username": "admin@example.com", "password": pw}

        async def form(self):
            return self._f

    auth = sqladmin_config.AdminAuth(secret_key="x" * 32)
    for _ in range(6):
        assert asyncio.run(auth.login(Req("6.6.6.6", "mala"))) is False
    assert asyncio.run(auth.login(Req("6.6.6.6", "Admin12345!"))) is False   # atacante bloqueado
    assert asyncio.run(auth.login(Req("1.1.1.1", "Admin12345!"))) is True    # el admin entra
