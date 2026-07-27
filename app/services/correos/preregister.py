"""
Correos Preregister API — registers a shipment and returns a shipmentCode
(tracking code) that is shown to the customer and used for label generation.

API spec: POST {CORREOS_API_BASE}/admissions/preregister/api/v1/delivery
Swagger: backend/docs/correos-api/preregister_pub.json

Synchronous httpx. In mock mode returns a deterministic fake code so the
order/email flow works end-to-end without a Correos contract.
"""
import logging

import httpx

from app.config import settings
from app.services.correos.auth import get_auth_headers

logger = logging.getLogger("cremacuadrado.correos")

_TIMEOUT = httpx.Timeout(20.0)

# Province name → Correos 2-digit code mapping (Spain)
_PROVINCE_CODES = {
    "álava": "01", "alava": "01", "albacete": "02", "alicante": "03",
    "almería": "04", "almeria": "04", "ávila": "05", "avila": "05",
    "badajoz": "06", "baleares": "07", "illes balears": "07",
    "barcelona": "08", "burgos": "09", "cáceres": "10", "caceres": "10",
    "cádiz": "11", "cadiz": "11", "castellón": "12", "castellon": "12",
    "ciudad real": "13", "córdoba": "14", "cordoba": "14", "coruña": "15",
    "a coruña": "15", "cuenca": "16", "girona": "17", "gerona": "17",
    "granada": "18", "guadalajara": "19", "guipúzcoa": "20", "gipuzkoa": "20",
    "huelva": "21", "huesca": "22", "jaén": "23", "jaen": "23",
    "león": "24", "leon": "24", "lleida": "25", "lérida": "25",
    "la rioja": "26", "lugo": "27", "madrid": "28", "málaga": "29",
    "malaga": "29", "murcia": "30", "navarra": "31", "ourense": "32",
    "orense": "32", "asturias": "33", "palencia": "34",
    "las palmas": "35", "pontevedra": "36", "salamanca": "37",
    "santa cruz de tenerife": "38", "tenerife": "38", "cantabria": "39",
    "segovia": "40", "sevilla": "41", "soria": "42", "tarragona": "43",
    "teruel": "44", "toledo": "45", "valencia": "46", "valladolid": "47",
    "vizcaya": "48", "bizkaia": "48", "zamora": "49", "zaragoza": "50",
    "ceuta": "51", "melilla": "52",
}


def _province_code(province: str) -> str:
    """Convert province name to 2-digit INE code. Falls back to first 2 chars of postal code."""
    if not province:
        return ""
    # Already a 2-digit code
    if len(province) <= 2 and province.isdigit():
        return province
    return _PROVINCE_CODES.get(province.lower().strip(), "")


def _province_code_from_cp(cp: str) -> str:
    """Extract 2-digit province code from postal code."""
    if cp and len(cp) >= 2:
        return cp[:2]
    return ""


def _preregister_url() -> str:
    return f"{settings.CORREOS_API_BASE}/admissions/preregister/api/v1/delivery"


def build_payload(order, weight_grams: int) -> dict:
    """Build the Correos preregister request body from an order."""
    addr = order.shipping_address or {}
    first_name = addr.get("first_name", "")
    last_name = addr.get("last_name", "")
    street = addr.get("street", "")
    street_2 = addr.get("street_2", "")
    cp = addr.get("postal_code", "")
    province_name = addr.get("province", "")
    province_code = _province_code(province_name) or _province_code_from_cp(cp)

    sender_province_code = (
        _province_code(settings.CORREOS_SENDER_PROVINCE)
        or _province_code_from_cp(settings.CORREOS_SENDER_POSTAL_CODE)
    )

    return {
        "errorCodeLanguage": "spa",
        "shipments": [
            {
                "product": settings.CORREOS_SERVICE_CODE,
                "deliveryMethod": "DOUAOF",
                "packagesNumber": "1",
                "totalWeight": str(weight_grams),
                "contractNumber": settings.CORREOS_NUM_CONTRATO,
                "clientNumber": settings.CORREOS_NUM_SOLICITANTE,
                "labellerCode": settings.CODIGO_ETIQUETADOR,
                "shipmentReference1": (order.order_number or "")[:20],
                "packages": [
                    {
                        "packageId": (order.order_number or "")[:30],
                        "packageWeightGrams": str(weight_grams),
                    }
                ],
                "addressee": {
                    "name": first_name,
                    "lastName1": last_name,
                    "address": street,
                    "addressComplement": street_2,
                    "locality": addr.get("city", ""),
                    "province": province_code,
                    "cp": cp,
                    "country": "ESP",
                    "contactPhone": addr.get("phone", ""),
                    "email": order.customer_email or "",
                },
                "sender": {
                    "name": settings.CORREOS_SENDER_NAME,
                    "company": settings.CORREOS_SENDER_NAME,
                    "address": settings.CORREOS_SENDER_ADDRESS,
                    "locality": settings.CORREOS_SENDER_CITY,
                    "province": sender_province_code,
                    "cp": settings.CORREOS_SENDER_POSTAL_CODE,
                    "country": "ESP",
                    "contactPhone": settings.CORREOS_SENDER_PHONE,
                    "email": settings.CORREOS_SENDER_EMAIL,
                },
                "notificaciones": [
                    {
                        "canal": "EMAIL",
                        "evento": "ENTREGA",
                        "email": order.customer_email or "",
                    }
                ],
            }
        ],
    }


def preregister_shipment(order, weight_grams: int) -> dict:
    """
    Register the shipment in Correos and return:
      {
        "shipment_code": "PQ...",     # expedition/shipment code
        "package_code": "PQ...",      # individual package code (localizador for tracking)
        "request": <payload>,
        "response": <raw response>,
      }

    In mock mode returns a fake code without any network call.
    """
    payload = build_payload(order, weight_grams)

    if not settings.CORREOS_ENABLED:
        mock_code = f"MOCK{order.id:08d}"
        logger.info("Correos mock preregister: order=%s code=%s", order.order_number, mock_code)
        return {
            "shipment_code": mock_code,
            "package_code": mock_code,
            "request": payload,
            "response": {"mock": True},
        }

    headers = get_auth_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.post(_preregister_url(), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Parse response: { "result": "1", "shipments": [{ "shipmentCode": "PQ...", "packages": [{ "packageCode": "PQ..." }] }] }
    result = data.get("result", "0")
    shipments = data.get("shipments", [])

    if result != "1" or not shipments:
        # Check for validation errors
        errors = []
        for s in shipments:
            err_count = s.get("validationErrorCount", 0)
            if err_count:
                err_list = s.get("error", [])
                errors.extend(err_list if isinstance(err_list, list) else [err_list])
        raise ValueError(f"Correos preregister failed (result={result}): {errors or data}")

    ship = shipments[0]
    shipment_code = ship.get("shipmentCode", "")
    package_code = ""
    packages = ship.get("packages", [])
    if packages:
        package_code = packages[0].get("packageCode", "")

    if not shipment_code and not package_code:
        raise ValueError(f"Correos preregister returned no codes: {data}")

    logger.info(
        "Correos preregister OK: order=%s shipmentCode=%s packageCode=%s",
        order.order_number, shipment_code, package_code,
    )
    return {
        "shipment_code": shipment_code,
        "package_code": package_code,
        "request": payload,
        "response": data,
    }


def cancel_shipment(package_code: str) -> dict:
    """
    Cancel a preregistered shipment.
    POST {CORREOS_API_BASE}/admissions/preregister/api/v1/delivery/annulment
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock cancel: %s", package_code)
        return {"mock": True, "message": "Preregistro anulado (mock)"}

    url = f"{settings.CORREOS_API_BASE}/admissions/preregister/api/v1/delivery/annulment"
    headers = get_auth_headers()
    payload = {
        "errorCodeLanguage": "spa",
        "packageCode": package_code,
    }
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Correos cancel OK: %s → %s", package_code, data.get("message", ""))
    return data
