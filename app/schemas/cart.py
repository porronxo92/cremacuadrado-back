"""Cart-related schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


class CartItemBase(BaseModel):
    """Base cart item schema."""
    product_id: int
    quantity: int = Field(default=1, ge=1)


class CartItemCreate(CartItemBase):
    """Cart item creation schema."""
    pass


class CartItemUpdate(BaseModel):
    """Cart item update schema."""
    quantity: int = Field(..., ge=1)


class CartItemResponse(BaseModel):
    """Cart item response schema."""
    id: int
    product_id: int
    product_name: str
    product_slug: str
    product_image: Optional[str]
    product_price: Decimal
    quantity: int
    price_at_add: Decimal
    total: Decimal
    is_available: bool
    stock_available: int
    
    model_config = {"from_attributes": True}


class ApplyCoupon(BaseModel):
    """Apply coupon request schema."""
    code: str = Field(..., min_length=1, max_length=50)


class CouponInfo(BaseModel):
    """Coupon info in cart."""
    code: str
    discount_type: str
    discount_value: Decimal
    discount_amount: Decimal


class CartResponse(BaseModel):
    """Cart response schema."""
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
