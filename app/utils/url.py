"""URL utilities for normalizing image paths."""
from app.config import settings


def normalize_image_url(url: str | None) -> str | None:
    """Return an absolute URL for the given image path.

    Priority:
      1. Already absolute → return as-is (blob CDN, external, or already rewritten).
      2. /images/... (Vercel Blob pathname) → prepend BLOB_BASE_URL if set.
      3. /assets/... → rewrite to /static/... for FastAPI StaticFiles, then fall through.
      4. /static/... or any other relative path → prepend BASE_URL if set.
    """
    if not url:
        return None

    if url.startswith("http"):
        return url

    # Blob pathnames are stored without /static/ prefix
    if url.startswith("/images/") and settings.BLOB_BASE_URL:
        return f"{settings.BLOB_BASE_URL}{url}"

    # Legacy Angular asset paths — remap to FastAPI static mount
    if url.startswith("/assets"):
        url = url.replace("/assets", "/static", 1)

    if settings.BASE_URL:
        return f"{settings.BASE_URL}{url}"

    return url
