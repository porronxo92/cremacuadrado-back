"""Coupon (discount code) schemas for admin management."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_discount(c) -> None:
    """free_shipping: valor 0 y sin tope; percent/fixed: valor > 0 (percent <= 100)."""
    if c.discount_type == "free_shipping":
        c.discount_value = Decimal("0")
        c.max_discount_amount = None
        return
    if c.discount_value is None:
        return
    if c.discount_value <= 0:
        raise ValueError("El valor del descuento debe ser mayor que 0")
    if c.discount_type == "percent" and c.discount_value > 100:
        raise ValueError("El porcentaje no puede superar 100")


class CouponBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    discount_type: str = Field(..., pattern="^(percent|fixed|free_shipping)$")
    discount_value: Decimal = Field(Decimal("0"), ge=0)
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

    @model_validator(mode="after")
    def check_discount(self):
        _normalize_discount(self)
        return self


class CouponCreate(CouponBase):
    pass


class CouponUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=255)
    discount_type: Optional[str] = Field(None, pattern="^(percent|fixed|free_shipping)$")
    discount_value: Optional[Decimal] = Field(None, ge=0)
    min_order_amount: Optional[Decimal] = None
    max_discount_amount: Optional[Decimal] = None
    usage_limit: Optional[int] = Field(None, gt=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def check_discount(self):
        # En actualizaciones parciales solo se valida si llega el tipo
        if self.discount_type is not None:
            _normalize_discount(self)
        elif self.discount_value is not None and self.discount_value < 0:
            raise ValueError("El valor del descuento no puede ser negativo")
        return self


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
