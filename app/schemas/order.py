"""Order-related schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


# =============================================================================
# Shipping schemas
# =============================================================================

class ShippingCostResponse(BaseModel):
    """Shipping cost calculation response."""
    cost: Decimal
    free_shipping_threshold: Decimal
    amount_for_free_shipping: Optional[Decimal]
    message: Optional[str]


# =============================================================================
# Coupon schemas
# =============================================================================

class CouponResponse(BaseModel):
    """Coupon validation response."""
    code: str
    description: Optional[str]
    discount_type: str
    discount_value: Decimal
    min_order_amount: Decimal
    is_valid: bool
    message: Optional[str]
    
    model_config = {"from_attributes": True}


# =============================================================================
# Checkout schemas
# =============================================================================

class AddressInput(BaseModel):
    """Address input for checkout."""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    street: str = Field(..., min_length=1, max_length=255)
    street_2: Optional[str] = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    province: str = Field(..., min_length=1, max_length=100)
    postal_code: str = Field(..., min_length=4, max_length=10)
    country: str = Field(default="España", max_length=100)
    phone: str = Field(..., min_length=9, max_length=20)


class CheckoutCreate(BaseModel):
    """Checkout creation schema."""
    shipping_address: AddressInput
    billing_address: Optional[AddressInput] = None
    same_billing_address: bool = True
    guest_email: Optional[EmailStr] = None  # Required for guest checkout
    customer_notes: Optional[str] = None
    save_address: bool = False  # Save address to user profile
    coupon_code: Optional[str] = None


class PaymentIntentResponse(BaseModel):
    """Payment intent response (mock for MVP)."""
    payment_intent_id: str
    client_secret: str
    amount: int  # Amount in cents
    currency: str = "eur"


class CheckoutValidation(BaseModel):
    """Checkout validation response."""
    is_valid: bool
    errors: List[str]
    subtotal: Decimal
    shipping_cost: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


# =============================================================================
# Order schemas
# =============================================================================

class OrderItemResponse(BaseModel):
    """Order item response schema."""
    id: int
    product_id: Optional[int]
    product_name: str
    product_sku: Optional[str]
    product_image_url: Optional[str]
    quantity: int
    unit_price: Decimal
    total: Decimal
    
    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    """Order response schema."""
    id: int
    order_number: str
    status: str
    subtotal: Decimal
    shipping_cost: Decimal
    discount: Decimal
    coupon_code: Optional[str]
    tax: Decimal
    total: Decimal
    shipping_address: dict
    billing_address: Optional[dict]
    payment_method: Optional[str]
    tracking_number: Optional[str]
    customer_notes: Optional[str]
    items: List[OrderItemResponse]
    item_count: int
    created_at: datetime
    paid_at: Optional[datetime]
    shipped_at: Optional[datetime]
    delivered_at: Optional[datetime]
    
    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    """Order list item schema."""
    id: int
    order_number: str
    status: str
    total: Decimal
    item_count: int
    created_at: datetime
    
    model_config = {"from_attributes": True}


class OrderStatusUpdate(BaseModel):
    """Order status update schema (admin)."""
    status: str = Field(..., pattern="^(pending|paid|processing|shipped|delivered|cancelled|refunded)$")
    tracking_number: Optional[str] = None
    admin_notes: Optional[str] = None


class CompleteCheckout(BaseModel):
    """Complete checkout after payment."""
    payment_intent_id: str
    payment_method: str = "card"
