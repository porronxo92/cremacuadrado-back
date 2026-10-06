"""
Rellena las coordenadas de los puntos de venta que no las tienen (necesarias
para el mapa de /puntos-de-venta). Usa OpenStreetMap Nominatim (1 petición/s).
Los que solo se localicen por ciudad quedan como "approximate": conviene
completar su dirección en el admin o poner las coordenadas a mano.

Usage:
    cd backend
    python scripts/geocode_points_of_sale.py            # solo los que no tienen coordenadas
    python scripts/geocode_points_of_sale.py --all      # recalcula todos salvo los "manual"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.models  # noqa: E402,F401
from app.models.database import SessionLocal  # noqa: E402
from app.models.point_of_sale import PointOfSale  # noqa: E402
from app.services.geocoding import geocode_point_of_sale  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        q = db.query(PointOfSale)
        if not args.all:
            q = q.filter(PointOfSale.latitude.is_(None))
        for store in q.order_by(PointOfSale.id).all():
            if store.geo_precision == "manual":
                continue
            found = geocode_point_of_sale(store)
            db.commit()
            print(f"{store.name} ({store.city}): "
                  f"{f'{store.latitude}, {store.longitude} [{store.geo_precision}]' if found else 'NO ENCONTRADO'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
