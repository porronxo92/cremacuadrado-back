"""
Compliance models: desistimientos, historial de precios, consentimientos y
registro de accesos del admin (database/scripts/018-022).
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.models.database import Base


class WithdrawalRequest(Base):
    """Solicitud de desistimiento (14 días) — 018_withdrawals.sql."""
    __tablename__ = "withdrawal_requests"

    STATUSES = ("received", "accepted", "refunded", "rejected")

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    items_text = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    within_term = Column(Boolean, nullable=False, default=True)
    status = Column(String(20), nullable=False, default="received")
    admin_notes = Column(Text, nullable=True)
    ip = Column(String(45), nullable=True)
    ack_sent_at = Column(DateTime, nullable=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("Order")


class PriceHistory(Base):
    """Periodos de precio por variante (Omnibus) — 019_price_history.sql."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Numeric(10, 2), nullable=False)
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)


class ConsentRecord(Base):
    """Prueba de consentimiento (RGPD art. 7.1) — 020_consents.sql."""
    __tablename__ = "consent_records"

    id = Column(Integer, primary_key=True, index=True)
    subject_email = Column(String(255), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    purpose = Column(String(50), nullable=False)
    granted = Column(Boolean, nullable=False)
    policy_version = Column(String(20), nullable=False)
    source = Column(String(50), nullable=False)
    ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AdminAuditLog(Base):
    """Accesos del personal a datos personales — 022_admin_audit_log.sql."""
    __tablename__ = "admin_audit_log"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    admin_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    admin_email = Column(String(255), nullable=True)
    action = Column(String(20), nullable=False)
    path = Column(String(500), nullable=False)
    query = Column(String(500), nullable=True)
    status_code = Column(Integer, nullable=True)
    ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
