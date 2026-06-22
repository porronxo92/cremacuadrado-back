"""
Correos España shipping integration.

Public entry point: create_shipment_for_order(db, order) — generates a Correos
shipment (preregister → localizador) for a paid order and persists a Shipment row.

While settings.CORREOS_ENABLED is False the whole package runs in mock mode and
returns a fake localizador, so the payment/order flow works without a signed
Correos contract.
"""
from app.services.correos.service import create_shipment_for_order

__all__ = ["create_shipment_for_order"]
