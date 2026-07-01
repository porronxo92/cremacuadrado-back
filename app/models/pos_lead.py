"""
Point-of-sale (B2B retail) lead model — leads captured from the /para-tiendas
landing page form.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime

from app.models.database import Base


class PosLead(Base):
    """Lead captured from the /para-tiendas B2B landing page form."""
    __tablename__ = "pos_leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    establishment_name = Column(String(255), nullable=False)
    city = Column(String(255), nullable=False)
    establishment_type = Column(String(50), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(30), nullable=False)
    stage = Column(String(100), nullable=False, default="Solicitud punto de venta recibida")
    status = Column(String(20), nullable=False, default="new")  # new | contacted | sample_sent | closed_won | closed_lost
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<PosLead {self.email} ({self.establishment_name})>"
