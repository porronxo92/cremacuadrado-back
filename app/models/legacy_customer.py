"""
Clientes históricos de la web antigua (029_legacy_customers.sql).
Al registrarse con ese email y verificarlo reciben un cupón personal del 20 % durante el periodo de LegacyProgram.
"""
from datetime import datetime
from sqlalchemy import Column, Date, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.models.database import Base


class LegacyCustomer(Base):
    __tablename__ = "legacy_customers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)  # siempre en minúsculas
    first_name = Column(String(100), nullable=True)
    source = Column(String(50), default="web_antigua", nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True)
    benefit_granted_at = Column(DateTime, nullable=True)
    welcome_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    coupon = relationship("Coupon")

    def __repr__(self):
        return f"<LegacyCustomer {self.email}>"


class LegacyProgram(Base):
    """Periodo de la promoción (fila única). Fechas en hora peninsular, ambas incluidas."""
    __tablename__ = "legacy_program"

    id = Column(Integer, primary_key=True, default=1)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
