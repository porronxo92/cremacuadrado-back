"""
Cliente síncrono para el store PRIVADO de Vercel Blob ("cremacuadrado-invoices").

Contrato REST replicado de @vercel/blob 2.x (no hay SDK oficial para Python):
  - Subida:  PUT  https://vercel.com/api/blob/?pathname=<ruta>
             cabeceras authorization, x-api-version, x-vercel-blob-store-id,
             x-vercel-blob-access: private
  - Lectura: GET  https://<storeId>.private.blob.vercel-storage.com/<ruta>
             con authorization: Bearer <token>
  - Borrado: POST https://vercel.com/api/blob/delete  {"urls": [...]}

Los blobs privados no son accesibles sin el token: el backend descarga el PDF
y lo sirve tras comprobar los permisos. Nunca se expone la URL del blob.
"""
import logging
from urllib.parse import quote, urlencode

import httpx

from app.config import settings

logger = logging.getLogger("cremacuadrado.blob")

_API_URL = "https://vercel.com/api/blob"
_API_VERSION = "12"
_TIMEOUT = 30.0


class BlobNotFound(Exception):
    """El blob solicitado no existe en el store."""


class BlobNotConfigured(RuntimeError):
    """Falta BLOB_INVOICE_READ_WRITE_TOKEN."""


def _token() -> str:
    token = settings.BLOB_INVOICE_READ_WRITE_TOKEN
    if not token:
        raise BlobNotConfigured(
            "BLOB_INVOICE_READ_WRITE_TOKEN no está configurado. "
            "Conecta el store 'cremacuadrado-invoices' al proyecto en Vercel."
        )
    return token


def store_id() -> str:
    """Id del store, sin el prefijo 'store_'.

    Se toma de BLOB_INVOICE_STORE_ID o, si falta, del propio token
    (formato vercel_blob_rw_<storeId>_<secreto>), igual que hace @vercel/blob.
    """
    configured = (settings.BLOB_INVOICE_STORE_ID or "").strip()
    if configured:
        return configured[len("store_"):] if configured.startswith("store_") else configured
    parts = _token().split("_")
    if len(parts) < 4 or not parts[3]:
        raise BlobNotConfigured("No se puede deducir el store id del token del Blob de facturas")
    return parts[3]


def is_configured() -> bool:
    return bool(settings.BLOB_INVOICE_READ_WRITE_TOKEN)


def _blob_url(pathname: str) -> str:
    return f"https://{store_id()}.private.blob.vercel-storage.com/{quote(pathname)}"


def upload(content: bytes, pathname: str, content_type: str = "application/pdf") -> str:
    """Sube *content* a *pathname* (sobrescribe si existe). Devuelve el pathname."""
    headers = {
        "authorization": f"Bearer {_token()}",
        "x-api-version": _API_VERSION,
        "x-vercel-blob-store-id": store_id(),
        "x-vercel-blob-access": "private",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
        "x-content-type": content_type,
        "x-content-length": str(len(content)),
    }
    url = f"{_API_URL}/?{urlencode({'pathname': pathname})}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.put(url, content=content, headers=headers)
    if response.status_code >= 400:
        logger.error("Blob upload failed: pathname=%s status=%s body=%s",
                     pathname, response.status_code, response.text[:300])
        response.raise_for_status()
    return response.json().get("pathname", pathname)


def download(pathname: str) -> bytes:
    """Descarga el contenido de un blob privado. Lanza BlobNotFound si no existe."""
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        response = client.get(
            _blob_url(pathname),
            params={"cache": "0"},
            headers={"authorization": f"Bearer {_token()}"},
        )
    if response.status_code == 404:
        raise BlobNotFound(pathname)
    response.raise_for_status()
    return response.content


def delete(pathname: str) -> None:
    """Borra un blob (solo para limpiar subidas huérfanas; las facturas no se borran)."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            f"{_API_URL}/delete",
            json={"urls": [_blob_url(pathname)]},
            headers={
                "authorization": f"Bearer {_token()}",
                "x-api-version": _API_VERSION,
                "x-vercel-blob-store-id": store_id(),
                "content-type": "application/json",
            },
        )
    response.raise_for_status()
