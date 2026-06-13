"""URL utilities for normalizing image paths."""
from app.config import settings


def normalize_image_url(url: str | None) -> str | None:
    """Convert relative image path to absolute if BASE_URL is set."""
    if not url:
        return None

    # Map /assets to /static (how FastAPI mounts them)
    if url.startswith("/assets"):
        url = url.replace("/assets", "/static", 1)

    # If BASE_URL is set, make it absolute; otherwise keep relative
    if settings.BASE_URL and not url.startswith("http"):
        return f"{settings.BASE_URL}{url}"

    return url
