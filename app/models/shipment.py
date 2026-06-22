"""
Shipment models — Correos España integration.
One Shipment per Order (MVP); ShipmentEvent stores tracking timeline.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.models.database import Base


class Shipment(Base):
    """Shipment created in Correos for a paid order."""
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    localizador = Column(String(50), nullable=True, index=True)  # Correos tracking code
    service_code = Column(String(20), nullable=True)
    weight_grams = Column(Integer, nullable=True)
    label_url = Column(String(500), nullable=True)
    status = Column(String(30), default="created", nullable=False)
    correos_request = Column(JSON, nullable=True)
    correos_response = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="shipment")
    events = relationship(
        "ShipmentEvent",
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.occurred_at",
    )

    def __repr__(self):
        return f"<Shipment order={self.order_id} localizador={self.localizador}>"


class ShipmentEvent(Base):
    """A single tracking event from Correos for a shipment."""
    __tablename__ = "shipment_events"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(30), nullable=True)
    description = Column(String(255), nullable=True)
    status = Column(String(30), nullable=True)
    occurred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    shipment = relationship("Shipment", back_populates="events")

    def __repr__(self):
        return f"<ShipmentEvent {self.code} shipment={self.shipment_id}>"
