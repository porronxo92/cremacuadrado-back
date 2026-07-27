"""
Correos España shipping integration.

Modules:
  - auth.py       — CorreosID OAuth2 token management
  - preregister.py — Register shipments (POST /delivery)
  - labels.py     — Generate shipping label PDFs
  - tracking.py   — Query tracking events (trackpub)
  - pickups.py    — Request package pickups (requests/recogidas)
  - service.py    — Orchestration (payment → shipment creation)

Public entry point: create_shipment_for_order(db, order) — generates a Correos
shipment (preregister → localizador) for a paid order and persists a Shipment row.

While settings.CORREOS_ENABLED is False the whole package runs in mock mode and
returns a fake localizador, so the payment/order flow works without a signed
Correos contract.
"""
from app.services.correos.service import create_shipment_for_order
from app.services.correos.labels import get_label_pdf, get_labels_pdf_multi
from app.services.correos.tracking import get_tracking_events, sync_tracking_for_shipment, poll_active_shipments
from app.services.correos.pickups import request_pickup, get_pickup_requests, cancel_pickup
from app.services.correos.preregister import cancel_shipment

__all__ = [
    "create_shipment_for_order",
    "get_label_pdf",
    "get_labels_pdf_multi",
    "get_tracking_events",
    "sync_tracking_for_shipment",
    "poll_active_shipments",
    "request_pickup",
    "get_pickup_requests",
    "cancel_pickup",
    "cancel_shipment",
]
