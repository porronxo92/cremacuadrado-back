"""
Invoice models — facturas emitidas (ver database/scripts/016_invoices.sql).

Una factura es inmutable una vez emitida: los datos fiscales se guardan como
copias (snapshots) y un trigger en PostgreSQL impide modificarlos o borrarlos.
Solo cambian el estado del PDF en Vercel Blob y los contadores de envío.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.database import Base

# JSONB en PostgreSQL, JSON genérico en otros motores (tests con SQLite)
_JSON = JSON().with_variant(JSONB(), "postgresql")


class InvoiceSequence(Base):
    """Último número emitido por serie y año."""
    __tablename__ = "invoice_sequences"

    series = Column(String(10), primary_key=True)
    year = Column(Integer, primary_key=True)
    last_number = Column(Integer, nullable=False, default=0)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("series", "year", "number"),)

    TYPE_SIMPLIFIED = "simplified"
    TYPE_FULL = "full"
    TYPE_CORRECTIVE = "corrective"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(30), unique=True, nullable=False)
    series = Column(String(10), nullable=False)
    year = Column(Integer, nullable=False)
    number = Column(Integer, nullable=False)
    invoice_type = Column(String(20), nullable=False)

    order_id = Column(Integer, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    rectifies_invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=True)
    refund_id = Column(Integer, ForeignKey("refunds.id", ondelete="RESTRICT"), nullable=True)

    issued_at = Column(DateTime, nullable=False)  # UTC
    seller_snapshot = Column(_JSON, nullable=False)
    buyer_snapshot = Column(_JSON, nullable=False)
    lines_snapshot = Column(_JSON, nullable=False)

    tax_base = Column(Numeric(10, 2), nullable=False)
    tax_rate = Column(Numeric(5, 4), nullable=False)
    tax_amount = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="EUR")

    previous_hash = Column(String(64), nullable=True)
    record_hash = Column(String(64), nullable=False)

    blob_pathname = Column(String(255), nullable=True)
    pdf_sha256 = Column(String(64), nullable=True)
    pdf_size = Column(Integer, nullable=True)
    pdf_status = Column(String(20), nullable=False, default="pending")  # pending | stored | failed

    sent_count = Column(Integer, nullable=False, default=0)
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    order = relationship("Order")
    user = relationship("User")
    rectifies = relationship("Invoice", remote_side=[id])

    @property
    def is_corrective(self) -> bool:
        return self.invoice_type == self.TYPE_CORRECTIVE

    @property
    def pdf_filename(self) -> str:
        return f"Factura_{self.invoice_number}.pdf"

    def __repr__(self):
        return f"<Invoice {self.invoice_number}>"
