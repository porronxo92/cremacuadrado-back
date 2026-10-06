"""
Geocodificación de puntos de venta con OpenStreetMap Nominatim (gratuito, sin
clave). Política de uso: máx. 1 petición/segundo y un User-Agent identificable.
Solo se llama desde acciones del admin y del script de backfill, nunca desde
visitas públicas.
"""
import logging
import time
from decimal import Decimal
from typing import Optional

import httpx

from app.config import settings
from app.models.point_of_sale import PointOfSale

logger = logging.getLogger("cremacuadrado.geocoding")

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_last_call = 0.0


def geocode(query: str | None = None, *, street: str | None = None, city: str | None = None,
            near: tuple[Decimal, Decimal] | None = None) -> Optional[tuple[Decimal, Decimal]]:
    """
    Coordenadas (lat, lng) en España, o None. Con `street`/`city` hace una
    búsqueda estructurada (distingue la ciudad de la provincia homónima, p. ej.
    Ciudad Real); con `query`, búsqueda libre. `near` limita la búsqueda a
    unos 15 km alrededor de ese punto (evita calles homónimas de otros pueblos).
    """
    global _last_call
    wait = 1.1 - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    params = {"format": "json", "limit": 1, "countrycodes": "es"}
    if query:
        params["q"] = query
    else:
        if street:
            params["street"] = street
        if city and not near:
            params["city"] = city
    if near:
        lat, lng = float(near[0]), float(near[1])
        params["viewbox"] = f"{lng - 0.15},{lat + 0.15},{lng + 0.15},{lat - 0.15}"
        params["bounded"] = 1
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                _NOMINATIM,
                params=params,
                headers={"User-Agent": f"CremaCuadrado/1.0 ({settings.SMTP_INFO_FROM_EMAIL})"},
            )
        _last_call = time.monotonic()
        response.raise_for_status()
        results = response.json()
    except Exception as exc:
        logger.warning("Geocoding failed for %r: %s", params, exc)
        return None
    if not results:
        return None
    q = Decimal("0.000001")
    return Decimal(results[0]["lat"]).quantize(q), Decimal(results[0]["lon"]).quantize(q)


def geocode_point_of_sale(store: PointOfSale) -> bool:
    """
    Rellena latitude/longitude: con la dirección (exact); si no hay o no se
    encuentra, con el nombre del negocio; y en último caso el centro de la
    ciudad (approximate). No toca coordenadas puestas a mano.
    """
    city_coords = geocode(city=store.city)
    attempts = []
    if store.address:
        attempts.append(({"street": store.address, "near": city_coords}, "exact"))
    attempts.append(({"query": store.name, "near": city_coords}, "approximate"))
    for kwargs, precision in attempts:
        if kwargs.get("near") is None:
            continue
        coords = geocode(**kwargs)
        if coords:
            store.latitude, store.longitude = coords
            store.geo_precision = precision
            return True
    if city_coords:
        store.latitude, store.longitude = city_coords
        store.geo_precision = "approximate"
        return True
    return False
