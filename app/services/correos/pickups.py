"""
Correos Requests (Recogidas) API — request package pickup from your warehouse.

API spec: POST {CORREOS_API_BASE}/logistics/requests/api/v1/requests
Swagger: backend/docs/correos-api/requests.yaml

Creates a "sporadic standard" pickup request: Correos sends a driver to collect
packages from the configured sender address on a given date.
"""
import logging
from datetime import date, timedelta

import httpx

from app.config import settings
from app.services.correos.auth import get_auth_headers

logger = logging.getLogger("cremacuadrado.correos")

_TIMEOUT = httpx.Timeout(30.0)


def _requests_url() -> str:
    return f"{settings.CORREOS_API_BASE}/logistics/requests/api/v1/requests"


def request_pickup(
    pickup_date: date | None = None,
    estimated_shipments: int = 1,
    estimated_volume: int = 20,  # 20 = small packages
    observations: str = "",
) -> dict:
    """
    Create a sporadic pickup request at the sender address.

    Args:
        pickup_date: Date for pickup (defaults to tomorrow).
        estimated_shipments: Number of packages to collect.
        estimated_volume: Volume category (10=envelopes, 20=small, 30=medium, 40=big).
        observations: Free text notes for the driver.

    Returns:
        The full response from Correos (includes codRequests).

    Raises:
        ValueError: On invalid response.
        httpx.HTTPStatusError: On HTTP errors.
    """
    if pickup_date is None:
        pickup_date = date.today() + timedelta(days=1)

    if not settings.CORREOS_ENABLED:
        mock_code = f"SRMOCK{pickup_date.strftime('%d%m%Y')}001"
        logger.info("Correos mock pickup: date=%s code=%s", pickup_date, mock_code)
        return {
            "codRequests": mock_code,
            "address": settings.CORREOS_SENDER_ADDRESS,
            "locality": settings.CORREOS_SENDER_CITY,
            "postalCode": settings.CORREOS_SENDER_POSTAL_CODE,
            "requestDate": pickup_date.isoformat(),
            "mock": True,
        }

    # Province is the first 2 digits of postal code for Correos
    province = settings.CORREOS_SENDER_POSTAL_CODE[:2] if settings.CORREOS_SENDER_POSTAL_CODE else ""

    payload = [
        {
            "codContract": settings.CORREOS_NUM_CONTRATO,
            "codSpecificContract": settings.CORREOS_NUM_SOLICITANTE,
            "codAnnex": "091",  # 091 = packages
            "modalityType": "S",  # S = Standard
            "originSystem": "CEX",
            "requestDate": pickup_date.isoformat(),
            "estimatedShipments": estimated_shipments,
            "estimatedVolume": estimated_volume,
            "contactName": settings.CORREOS_SENDER_NAME,
            "phoneNumberContact": settings.CORREOS_SENDER_PHONE,
            "contactEmail": settings.CORREOS_SENDER_EMAIL,
            "address": settings.CORREOS_SENDER_ADDRESS,
            "locality": settings.CORREOS_SENDER_CITY,
            "province": province,
            "postalCode": settings.CORREOS_SENDER_POSTAL_CODE,
            "clientObservations": observations or f"Recogida CremaCuadrado - {estimated_shipments} paquete(s)",
        }
    ]

    headers = get_auth_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.post(_requests_url(), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Response is the created SolicitudRecogidaDTO (or list)
    if isinstance(data, list):
        result = data[0] if data else {}
    else:
        result = data

    cod_requests = result.get("codRequests", "")
    logger.info("Correos pickup request OK: code=%s date=%s", cod_requests, pickup_date)
    return result


def get_pickup_requests(contract: str | None = None) -> list[dict]:
    """
    List existing pickup requests.

    GET {CORREOS_API_BASE}/logistics/requests/api/v1/requests?contract={contract}
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock get pickups")
        return []

    params = {}
    if contract:
        params["contract"] = contract
    else:
        params["contract"] = settings.CORREOS_NUM_CONTRATO

    headers = get_auth_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.get(_requests_url(), headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, list):
        return data
    # Some responses wrap in a content key
    return data.get("content", [data])


def cancel_pickup(pickup_code: str) -> dict:
    """
    Cancel a pickup request.

    PATCH {CORREOS_API_BASE}/logistics/requests/api/v1/requests/cancelRequest/{id}
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock cancel pickup: %s", pickup_code)
        return {"mock": True, "codRequests": pickup_code, "statusPrivate": "CANCELADA"}

    url = f"{settings.CORREOS_API_BASE}/logistics/requests/api/v1/requests/cancelRequest/{pickup_code}"
    headers = get_auth_headers()
    payload = {"codRequests": pickup_code}

    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.patch(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Correos pickup cancelled: %s", pickup_code)
    return data
