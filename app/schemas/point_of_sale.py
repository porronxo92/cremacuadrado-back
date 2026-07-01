"""Schemas for the /puntos-de-venta public listing."""
from pydantic import BaseModel, ConfigDict


class PointOfSaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str
    instagram_url: str
    maps_url: str
