"""
Test configuration.

Settings exige SECRET_KEY y ADMIN_PASSWORD: se fijan antes de importar la app.
Los tests de servicios usan SQLite en memoria con solo las tablas necesarias
(los modelos con JSONB puro de PostgreSQL no se crean).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("EMAIL_ENABLED", "False")
os.environ["BLOB_INVOICE_READ_WRITE_TOKEN"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401
from app.models.database import Base  # noqa: E402
from app.models.invoice import Invoice, InvoiceSequence  # noqa: E402
from app.models.order import Coupon, Order, OrderItem  # noqa: E402
from app.models.payment import Refund  # noqa: E402
from app.models.user import User, Address, PasswordResetToken, EmailVerificationToken  # noqa: E402
from app.models.cart import Cart, CartItem  # noqa: E402
from app.models.compliance import AdminAuditLog, ConsentRecord, PriceHistory, WithdrawalRequest  # noqa: E402
from app.models.contact_lead import ContactLead  # noqa: E402
from app.models.lead import NewsletterLead  # noqa: E402
from app.models.pos_lead import PosLead  # noqa: E402
from app.models.product import Category, Product, ProductVariant, Review  # noqa: E402
from app.models.payment import StripeWebhookEvent  # noqa: E402
import app.services.price_history  # noqa: E402,F401  (listeners)

_TABLES = [
    User.__table__,
    Address.__table__,
    PasswordResetToken.__table__,
    EmailVerificationToken.__table__,
    Category.__table__,
    Product.__table__,
    ProductVariant.__table__,
    Review.__table__,
    PriceHistory.__table__,
    Cart.__table__,
    CartItem.__table__,
    Order.__table__,
    OrderItem.__table__,
    Coupon.__table__,
    Refund.__table__,
    InvoiceSequence.__table__,
    Invoice.__table__,
    WithdrawalRequest.__table__,
    ConsentRecord.__table__,
    AdminAuditLog.__table__,
    NewsletterLead.__table__,
    ContactLead.__table__,
    PosLead.__table__,
]


@pytest.fixture()
def db():
    # StaticPool + check_same_thread=False: TestClient ejecuta los endpoints en otro hilo
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=_TABLES)
    # stripe_webhook_events usa JSONB puro (no compila en SQLite): tabla mínima para la purga
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE stripe_webhook_events (id INTEGER PRIMARY KEY, stripe_event_id TEXT, "
            "event_type TEXT, payload TEXT, processed BOOLEAN, processed_at DATETIME, error TEXT, created_at DATETIME)"
        )
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
