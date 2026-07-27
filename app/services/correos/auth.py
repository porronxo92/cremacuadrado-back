"""
Correos API authentication — two-layer security model:

1. JWT Bearer token (CorreosID OAuth2 client_credentials flow):
   POST https://apioauthcid.correos.es/Api/Authorize/Token
   Credentials: CORREOS_CLIENT_ID + CORREOS_CLIENT_SECRET (from CorreosID portal)
   Response field: "idToken" (expiresIn is in MINUTES)

2. Client ID Enforcement (API Gateway policy, all endpoints):
   Headers: client_id: CLIENT_ID_API  /  client_secret: CLIENT_SECRET_API
   These come from the API subscription in the developers.correos.es portal.

Both are required on every request. get_auth_headers() returns all three headers.
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
    """Return a valid CorreosID JWT token, refreshing if expired."""
    if not settings.CORREOS_ENABLED:
        return "mock-token"

    now = time.time()
    # Reuse cached token until 60s before expiry
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    if not settings.CORREOS_OAUTH_URL:
        raise ValueError("CORREOS_OAUTH_URL is not configured")
    if not settings.CORREOS_CLIENT_ID or not settings.CORREOS_CLIENT_SECRET:
        raise ValueError("CORREOS_CLIENT_ID / CORREOS_CLIENT_SECRET not configured")

    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        body = {
            "grant_type": "client_credentials",
            "client_id": settings.CORREOS_CLIENT_ID,
            "client_secret": settings.CORREOS_CLIENT_SECRET,
        }
        if settings.CORREOS_SCOPE:
            body["scope"] = settings.CORREOS_SCOPE
        resp = client.post(
            settings.CORREOS_OAUTH_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Language": "es",
            },
        )
        if not resp.is_success:
            logger.error(
                "CorreosID auth failed %s — body: %s",
                resp.status_code,
                resp.text[:2000],
            )
        resp.raise_for_status()
        data = resp.json()

    # CorreosID returns "idToken" (not "access_token"), expiresIn is in MINUTES
    token = data.get("idToken") or data.get("access_token")
    if not token:
        raise ValueError(f"CorreosID response missing token field. Keys: {list(data.keys())}")
    expires_in_minutes = data.get("expiresIn", 30)
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + (expires_in_minutes * 60)
    logger.info("CorreosID token obtained (expiresIn=%smin)", expires_in_minutes)
    return _token_cache["access_token"]


def get_auth_headers() -> dict:
    """
    Return all required headers for Correos API calls:
    - Authorization: Bearer {jwt}  — JWT Validation policy (CorreosID)
    - client_id / client_secret    — Client ID Enforcement policy (API Gateway)
    """
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "client_id": settings.CLIENT_ID_API,
        "client_secret": settings.CLIENT_SECRET_API,
        "Content-Type": "application/json",
    }


def get_trackpub_headers() -> dict:
    """Alias for get_auth_headers() — trackpub uses the same header set."""
    return get_auth_headers()

    return headers
