"""
Vercel Blob storage service — wraps the Blob REST API v7.

All uploaded files are public (readable without token).
Pathnames follow the same structure as the old /static/images/ tree
so existing URL patterns in the DB remain valid after migration.
"""
import mimetypes

import httpx

from app.config import settings

_BLOB_API = "https://blob.vercel-storage.com"
_API_VERSION = "7"
# 30-day CDN cache for product / blog images
_DEFAULT_MAX_AGE = str(60 * 60 * 24 * 30)


async def upload(content: bytes, pathname: str, content_type: str | None = None) -> str:
    """Upload *content* to Vercel Blob at *pathname* and return the public URL.

    Args:
        content: Raw file bytes.
        pathname: Blob path, e.g. ``images/products/Crema Pistacho Pura/200gr/abc_img.jpg``.
        content_type: MIME type. Guessed from pathname if omitted.

    Returns:
        The public HTTPS URL of the uploaded blob.
    """
    if not settings.BLOB_PUBLIC_READ_WRITE_TOKEN:
        raise RuntimeError(
            "BLOB_PUBLIC_READ_WRITE_TOKEN is not set. "
            "Add it to your .env file and Vercel environment variables."
        )

    if content_type is None:
        guessed, _ = mimetypes.guess_type(pathname)
        content_type = guessed or "application/octet-stream"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.put(
            f"{_BLOB_API}/{pathname}",
            content=content,
            headers={
                "Authorization": f"Bearer {settings.BLOB_PUBLIC_READ_WRITE_TOKEN}",
                "x-api-version": _API_VERSION,
                "Content-Type": content_type,
                "x-add-random-suffix": "0",
                "x-cache-control-max-age": _DEFAULT_MAX_AGE,
            },
        )
        response.raise_for_status()
        return response.json()["url"]


async def delete(url: str) -> None:
    """Delete a blob by its public URL."""
    if not settings.BLOB_PUBLIC_READ_WRITE_TOKEN:
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            f"{_BLOB_API}/delete",
            json={"urls": [url]},
            headers={
                "Authorization": f"Bearer {settings.BLOB_PUBLIC_READ_WRITE_TOKEN}",
                "x-api-version": _API_VERSION,
            },
        )
        response.raise_for_status()
