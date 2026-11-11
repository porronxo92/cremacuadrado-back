"""
Antispam de los formularios públicos (contacto, newsletter, leads B2B, desistimiento).

- Honeypot: campo `website` oculto con CSS; una persona lo deja vacío.
- Cloudflare Turnstile: el frontend envía `turnstile_token` y aquí se valida.
  Sin TURNSTILE_SECRET_KEY no se comprueba (desarrollo local).
"""
import logging
from typing import Optional

import httpx
from fastapi import HTTPException, Request, status

from app.config import settings
from app.services.consents import client_ip

logger = logging.getLogger("cremacuadrado.antispam")

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
BOT_CHECK_FAILED = ("No hemos podido comprobar que no eres un robot. Recarga la página "
                    "y vuelve a intentarlo, o escríbenos a info@cremacuadrado.com")


def honeypot_hit(website: str, form: str) -> bool:
    if website.strip():
        logger.info("Honeypot hit, discarded: form=%s", form)
        return True
    return False


async def turnstile_ok(token: str, ip: Optional[str]) -> bool:
    """Valida el token de Cloudflare Turnstile. Si Cloudflare no responde, rechaza (fail closed)."""
    if not token:
        return False
    payload = {"secret": settings.TURNSTILE_SECRET_KEY, "response": token}
    if ip:
        payload["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(TURNSTILE_VERIFY_URL, data=payload)
        result = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Turnstile verification request failed")
        return False
    if not result.get("success"):
        logger.info("Turnstile rejected: %s", result.get("error-codes"))
    return bool(result.get("success"))


async def require_human(token: str, request: Request) -> None:
    """400 si Turnstile está configurado y el token no es válido."""
    if settings.TURNSTILE_SECRET_KEY and not await turnstile_ok(token, client_ip(request)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=BOT_CHECK_FAILED)
