"""
URL pública del frontend desde el que llega cada petición.

Los emails deben enlazar a la web en la que el cliente está comprando
(staging.cremacuadrado.com, prd.cremacuadrado.com, cremacuadrado.com…), no a un
valor fijo. El middleware `capture_site_url` guarda el Origin de la petición si
está en CORS_ORIGINS (lista blanca: nunca se usa un origen arbitrario). Para
procesos sin petición del navegador (webhook de Stripe) se usa la URL guardada en
el pedido (orders.site_url) con `use_site_url()`. Si no hay nada, SITE_URL.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from fastapi import Request

from app.config import settings

_current: ContextVar[Optional[str]] = ContextVar("site_url", default=None)


def allowed_origin(origin: Optional[str]) -> Optional[str]:
    if not origin:
        return None
    origin = origin.rstrip("/")
    allowed = {o.rstrip("/") for o in settings.CORS_ORIGINS}
    return origin if origin in allowed else None


def site_url() -> str:
    return (_current.get() or settings.SITE_URL).rstrip("/")


def request_site_url(request: Request) -> Optional[str]:
    return allowed_origin(request.headers.get("origin"))


@contextmanager
def use_site_url(url: Optional[str]) -> Iterator[None]:
    token = _current.set(allowed_origin(url) or _current.get())
    try:
        yield
    finally:
        _current.reset(token)


async def capture_site_url(request: Request, call_next):
    token = _current.set(request_site_url(request))
    try:
        return await call_next(request)
    finally:
        _current.reset(token)
