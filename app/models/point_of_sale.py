"""
Point of sale model — physical/partner stores listed on /puntos-de-venta.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime

from app.models.database import Base


class PointOfSale(Base):
    """A store or partner venue that sells Cremacuadrado, shown on /puntos-de-venta."""
    __tablename__ = "points_of_sale"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    city = Column(String(255), nullable=False, index=True)
    instagram_url = Column(String(500), nullable=False)
    maps_url = Column(String(500), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<PointOfSale {self.name} ({self.city})>"
