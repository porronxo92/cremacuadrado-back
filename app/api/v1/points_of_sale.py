"""Public points-of-sale listing for /puntos-de-venta."""
from typing import List

from fastapi import APIRouter

from app.api.deps import DbSession
from app.models.point_of_sale import PointOfSale
from app.schemas.point_of_sale import PointOfSaleResponse

router = APIRouter()


@router.get("", response_model=List[PointOfSaleResponse])
async def list_points_of_sale(db: DbSession):
    """List active points of sale, ordered by city and manual sort order."""
    stores = (
        db.query(PointOfSale)
        .filter(PointOfSale.is_active.is_(True))
        .order_by(PointOfSale.city, PointOfSale.sort_order)
        .all()
    )
    return stores
