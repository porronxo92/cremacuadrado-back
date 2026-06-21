"""URL utilities for normalizing image paths."""
from app.config import settings


def normalize_image_url(url: str | None) -> str | None:
    """Return an absolute URL for the given image path.

    Priority:
      1. Already absolute → return as-is (blob CDN, external, or already rewritten).
      2. /images/... (Vercel Blob pathname) → prepend BLOB_BASE_URL if set.
      3. /assets/... → return as-is (Angular frontend asset, resolved by the browser).
      4. /static/... or any other relative path → prepend BASE_URL if set.
    """
    if not url:
        return None

    if url.startswith("http"):
        return url

    # Blob pathnames stored as /images/... → full CDN URL
    if url.startswith("/images/") and settings.BLOB_BASE_URL:
        return f"{settings.BLOB_BASE_URL}{url}"

    # Angular frontend assets (/assets/...) are served by the SPA, not the backend.
    # Return as-is so the browser resolves them against the frontend origin.
    if url.startswith("/assets/"):
        return url

    if settings.BASE_URL:
        return f"{settings.BASE_URL}{url}"

    return url
