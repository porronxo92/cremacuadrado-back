"""Coupon (discount code) schemas for admin management."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CouponBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    discount_type: str = Field(..., pattern="^(percent|fixed)$")
    discount_value: Decimal = Field(..., gt=0)
    min_order_amount: Decimal = Decimal("0")
    max_discount_amount: Optional[Decimal] = None
    usage_limit: Optional[int] = Field(None, gt=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def uppercase_code(cls, v: str) -> str:
        return v.strip().upper()


class CouponCreate(CouponBase):
    pass


class CouponUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=255)
    discount_type: Optional[str] = Field(None, pattern="^(percent|fixed)$")
    discount_value: Optional[Decimal] = Field(None, gt=0)
    min_order_amount: Optional[Decimal] = None
    max_discount_amount: Optional[Decimal] = None
    usage_limit: Optional[int] = Field(None, gt=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None


class CouponResponse(CouponBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    used_count: int
    is_valid: bool
    created_at: datetime


class CouponAdminResponse(CouponResponse):
    """Coupon with redemption analytics for the admin panel."""
    redemptions: int = 0
    unique_customers: int = 0
    total_discount: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")


class CouponRedemptionItem(BaseModel):
    id: int
    order_id: int
    order_number: Optional[str] = None
    order_status: Optional[str] = None
    order_total: Optional[Decimal] = None
    user_id: Optional[int] = None
    email: Optional[str] = None
    customer_name: Optional[str] = None
    discount_amount: Decimal
    reverted_at: Optional[datetime] = None
    created_at: datetime
