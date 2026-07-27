"""
Correos Trackpub API — query shipment tracking events.

API spec: GET {CORREOS_API_BASE}/support/trackpub/api/v2/search/{shippingCode}
Swagger: backend/docs/correos-api/trackpub.yaml

IMPORTANT: trackpub uses DIFFERENT auth headers than other Correos APIs:
  - Authorization: Bearer {token}   (CorreosID JWT — same as other APIs)
  - client_id: {CLIENT_ID_API}      (API Gateway credential from developers.correos.es)
  - client_secret: {CLIENT_SECRET_API}

The polling job (poll_active_shipments) runs every 60 min via APScheduler,
queries Correos for active shipments, inserts ShipmentEvent rows, and updates
shipment/order status accordingly.
"""
import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.services.correos.auth import get_trackpub_headers

logger = logging.getLogger("cremacuadrado.correos")

_TIMEOUT = httpx.Timeout(20.0)

# Correos event phases → internal status mapping
# phase "1" descriptions contain ENTREGADO, phase values vary
_PHASE_STATUS_MAP = {
    "ENTREGADO": "delivered",
    "EN TRÁNSITO": "in_transit",
    "EN TRANSITO": "in_transit",
    "EN REPARTO": "out_for_delivery",
    "DEVUELTO": "returned",
    "INCIDENCIA": "incident",
    "RETENIDO": "held",
}

# Terminal statuses — stop polling when reached
TERMINAL_STATUSES = {"delivered", "returned", "cancelled"}


def _tracking_url(shipping_code: str) -> str:
    return f"{settings.CORREOS_API_BASE}/support/trackpub/api/v2/search/{shipping_code}"


def get_tracking_events(shipping_code: str) -> dict:
    """
    Query Correos tracking for a single shipment code.

    Returns the full response dict with 'events' list, shipment metadata, etc.
    In mock mode returns a fake response.
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock tracking: %s", shipping_code)
        return {
            "code": shipping_code,
            "events": [
                {
                    "eventCode": "MOCK001",
                    "eventDes": "PRE-ADMISION",
                    "summaryText": "Prerregistrado",
                    "expandedText": "Prerregistrado pendiente de depósito en Correos",
                    "eventDate": datetime.utcnow().strftime("%d/%m/%Y"),
                    "eventHours": datetime.utcnow().strftime("%H:%M:%S"),
                    "phase": "0",
                    "phaseDes": "PRERREGISTRADO",
                    "location": "MOCK",
                    "color": "V",
                }
            ],
            "mock": True,
        }

    headers = get_trackpub_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.get(
            _tracking_url(shipping_code),
            headers=headers,
            params={"languageCode": "ES"},
        )
        resp.raise_for_status()
        data = resp.json()

    logger.info(
        "Correos tracking OK: %s — %d events",
        shipping_code,
        len(data.get("events", [])),
    )
    return data


def _parse_event_datetime(event: dict) -> datetime | None:
    """Parse eventDate + eventHours from Correos format to datetime."""
    date_str = event.get("eventDate", "")
    hours_str = event.get("eventHours", "")
    if not date_str:
        return None
    try:
        if hours_str:
            return datetime.strptime(f"{date_str} {hours_str}", "%d/%m/%Y %H:%M:%S")
        return datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        return None


def _map_phase_to_status(phase_des: str) -> str:
    """Map Correos phaseDes to our internal status string."""
    if not phase_des:
        return "unknown"
    upper = phase_des.upper().strip()
    for key, status in _PHASE_STATUS_MAP.items():
        if key in upper:
            return status
    return "in_transit"  # safe default


def sync_tracking_for_shipment(db: Session, shipment) -> int:
    """
    Fetch tracking from Correos and sync ShipmentEvent rows.

    Returns the number of NEW events inserted.
    Idempotent: skips events already stored (by unique eventCode+eventDate).
    """
    from app.models.shipment import ShipmentEvent

    if not shipment.localizador:
        return 0

    try:
        data = get_tracking_events(shipment.localizador)
    except Exception as exc:
        logger.warning("Tracking fetch failed for %s: %s", shipment.localizador, exc)
        return 0

    events = data.get("events", [])
    if not events:
        return 0

    # Load existing event codes to avoid duplicates
    existing_codes = set()
    for ev in shipment.events:
        key = f"{ev.code}|{ev.occurred_at}"
        existing_codes.add(key)

    new_count = 0
    latest_status = None

    for ev in events:
        occurred_at = _parse_event_datetime(ev)
        event_code = ev.get("eventCode", "") or ev.get("uniqueCode", "")
        key = f"{event_code}|{occurred_at}"

        if key in existing_codes:
            continue

        phase_des = ev.get("phaseDes", "")
        status = _map_phase_to_status(phase_des)

        db_event = ShipmentEvent(
            shipment_id=shipment.id,
            code=event_code,
            description=ev.get("expandedText") or ev.get("summaryText") or ev.get("eventDes", ""),
            status=status,
            occurred_at=occurred_at,
        )
        db.add(db_event)
        existing_codes.add(key)
        new_count += 1
        latest_status = status

    # Update shipment status with the latest event
    if latest_status:
        shipment.status = latest_status
        shipment.updated_at = datetime.utcnow()

        # Update order status if terminal
        if latest_status == "delivered" and shipment.order:
            shipment.order.shipping_status = "delivered"
            shipment.order.delivered_at = datetime.utcnow()
        elif latest_status == "returned" and shipment.order:
            shipment.order.shipping_status = "returned"
        elif latest_status in ("in_transit", "out_for_delivery") and shipment.order:
            shipment.order.shipping_status = latest_status

    if new_count:
        logger.info("Tracking synced: %s — %d new events, status=%s", shipment.localizador, new_count, latest_status)

    return new_count


def poll_active_shipments() -> None:
    """
    Scheduled job: poll tracking for all non-terminal shipments.

    Called by APScheduler every 60 minutes. Opens its own DB session.
    """
    from app.models.database import SessionLocal
    from app.models.shipment import Shipment

    db = SessionLocal()
    try:
        active = (
            db.query(Shipment)
            .filter(
                Shipment.localizador.isnot(None),
                Shipment.status.notin_(list(TERMINAL_STATUSES) + ["failed"]),
            )
            .all()
        )

        if not active:
            return

        total_new = 0
        for shipment in active:
            new_events = sync_tracking_for_shipment(db, shipment)
            total_new += new_events

        if total_new:
            db.commit()
            logger.info("Tracking poll complete: %d shipments, %d new events", len(active), total_new)
    except Exception as exc:
        db.rollback()
        logger.error("Tracking poll failed: %s", exc, exc_info=True)
    finally:
        db.close()
