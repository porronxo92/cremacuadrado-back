"""
Correos Preregister API — registers a shipment and returns a `localizador`
(tracking code) that is shown to the customer and used for tracking.

Synchronous httpx. In mock mode returns a deterministic fake localizador so the
order/email flow works end-to-end without a Correos contract.
"""
import logging

import httpx

from app.config import settings
from app.services.correos.auth import get_access_token

logger = logging.getLogger("cremacuadrado.correos")

_TIMEOUT = httpx.Timeout(20.0)


def _preregister_url() -> str:
    return f"{settings.CORREOS_API_BASE}/preregistro/v2/preregistroenvios"


def build_payload(order, weight_grams: int) -> dict:
    """Build the Correos preregister request body from an order."""
    addr = order.shipping_address or {}
    full_name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
    street = addr.get("street", "")
    if addr.get("street_2"):
        street = f"{street}, {addr['street_2']}"

    return {
        "solicitante": settings.CORREOS_NUM_SOLICITANTE,
        "canalEntrada": "IW",
        "numContrato": settings.CORREOS_NUM_CONTRATO,
        "codigoServicio": settings.CORREOS_SERVICE_CODE,
        "referencia": order.order_number[:20],
        "remite": {
            "nombre": settings.CORREOS_SENDER_NAME,
            "direccion": settings.CORREOS_SENDER_ADDRESS,
            "localidad": settings.CORREOS_SENDER_CITY,
            "provincia": settings.CORREOS_SENDER_PROVINCE,
            "cp": settings.CORREOS_SENDER_POSTAL_CODE,
            "telefono": settings.CORREOS_SENDER_PHONE,
            "email": settings.CORREOS_SENDER_EMAIL,
        },
        "destinatario": {
            "nombre": full_name,
            "direccion": street,
            "localidad": addr.get("city", ""),
            "provincia": addr.get("province", ""),
            "cp": addr.get("postal_code", ""),
            "pais": addr.get("country", "España"),
            "telefono": addr.get("phone", ""),
            "email": order.customer_email or "",
        },
        "paquete": {
            "pesoFisico": weight_grams,
        },
        "notificaciones": [
            {"canal": "EMAIL", "evento": "ENTREGA", "email": order.customer_email or ""}
        ],
    }


def preregister_shipment(order, weight_grams: int) -> dict:
    """
    Register the shipment in Correos and return {"localizador", "request", "response"}.

    In mock mode returns a fake localizador without any network call.
    """
    payload = build_payload(order, weight_grams)

    if not settings.CORREOS_ENABLED:
        localizador = f"MOCK{order.id:08d}"
        logger.info("Correos mock preregister: order=%s localizador=%s", order.order_number, localizador)
        return {"localizador": localizador, "request": payload, "response": {"mock": True}}

    token = get_access_token()
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            _preregister_url(),
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # The exact field name depends on the Correos API version; try common keys.
    localizador = (
        data.get("localizador")
        or data.get("codEnvio")
        or data.get("codigoEnvio")
    )
    if not localizador:
        raise ValueError(f"Correos preregister returned no localizador: {data}")

    logger.info("Correos preregister OK: order=%s localizador=%s", order.order_number, localizador)
    return {"localizador": localizador, "request": payload, "response": data}
