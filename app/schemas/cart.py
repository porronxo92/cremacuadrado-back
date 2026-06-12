"""Cart-related schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


class CartItemCreate(BaseModel):
    product_variant_id: int
    quantity: int = Field(default=1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1)


class CartItemResponse(BaseModel):
    id: int
    product_id: int
    product_variant_id: int
    product_name: str
    product_slug: str
    variant_format: str        # '100g', '200g', '1kg'
    product_image: Optional[str]
    unit_price: Decimal
    price_at_add: Decimal
    quantity: int
    total: Decimal
    is_available: bool
    stock_available: int

    model_config = {"from_attributes": True}


class ApplyCoupon(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)


class CouponInfo(BaseModel):
    code: str
    discount_type: str
    discount_value: Decimal
    discount_amount: Decimal


class CartResponse(BaseModel):
    id: int
    item_count: int
    items: List[CartItemResponse]
    subtotal: Decimal
    coupon: Optional[CouponInfo]
    discount: Decimal
    shipping_cost: Decimal
    shipping_message: Optional[str]
    total: Decimal
    updated_at: datetime

    model_config = {"from_attributes": True}
