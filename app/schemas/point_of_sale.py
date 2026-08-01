"""Schemas for the /puntos-de-venta public listing and admin CRUD."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PointOfSaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str
    instagram_url: str
    maps_url: str


class PointOfSaleAdminResponse(PointOfSaleResponse):
    """Admin view — includes fields not shown on the public page."""
    is_active: bool
    sort_order: int


class PointOfSaleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    city: str = Field(..., min_length=1, max_length=255)
    instagram_url: str = Field(..., min_length=1, max_length=500)
    maps_url: str = Field(..., min_length=1, max_length=500)
    is_active: bool = True
    sort_order: int = 0


class PointOfSaleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    city: Optional[str] = Field(None, min_length=1, max_length=255)
    instagram_url: Optional[str] = Field(None, min_length=1, max_length=500)
    maps_url: Optional[str] = Field(None, min_length=1, max_length=500)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
