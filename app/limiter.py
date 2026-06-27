"""Shared slowapi rate limiter instance."""
from slowapi import Limiter


def _get_real_ip(request) -> str:
    """Return the real client IP, honouring X-Forwarded-For set by Vercel/proxies."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_get_real_ip)
