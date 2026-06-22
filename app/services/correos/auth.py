"""
CorreosID OAuth2 (client_credentials) — token with in-memory cache.

Synchronous (httpx.Client) because the caller runs inside the Stripe webhook
handler, which is a sync function. In mock mode (CORREOS_ENABLED=False) returns
a fake token without any network call.
"""
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger("cremacuadrado.correos")

# Module-level cache: { "access_token": str | None, "expires_at": epoch_seconds }
_token_cache: dict = {"access_token": None, "expires_at": 0.0}

_TIMEOUT = httpx.Timeout(15.0)


def get_access_token() -> str:
    """Return a valid CorreosID access token, refreshing if needed."""
    if not settings.CORREOS_ENABLED:
        return "mock-token"

    now = time.time()
    # Reuse cached token until 60s before expiry
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            settings.CORREOS_OAUTH_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.CORREOS_CLIENT_ID,
                "client_secret": settings.CORREOS_CLIENT_SECRET,
                "scope": "correos_api",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    logger.info("CorreosID token refreshed (expires_in=%ss)", data.get("expires_in", 3600))
    return _token_cache["access_token"]
