"""Admin-related schemas."""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.order import OrderResponse
from app.schemas.user import _validate_password_strength


class DashboardStats(BaseModel):
    """Dashboard statistics."""
    # All-time totals
    total_orders: int
    pending_orders: int
    total_revenue: Decimal
    total_customers: int

    # Today's stats
    orders_today: int
    revenue_today: Decimal
    
    # Period stats (default: last 30 days)
    orders_period: int
    revenue_period: Decimal
    average_order_value: Decimal
    
    # Product stats
    top_products: List[dict]  # [{product_name, quantity_sold, revenue}]
    
    # Order status breakdown
    orders_by_status: dict  # {status: count}
    
    # Comparison with previous period
    orders_growth: Optional[float]  # percentage
    revenue_growth: Optional[float]  # percentage

    # Extended analytics (period-aware)
    period_days: int = 30
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    paid_orders_period: int = 0
    new_customers_period: int = 0
    returning_customer_rate: Optional[float] = None  # % of buyers in period with >1 paid order ever
    daily_series: List[dict] = []  # [{date, orders, revenue}]
    top_coupons: List[dict] = []   # [{code, uses, discount}]
    abandoned_carts: int = 0
    abandoned_carts_value: Decimal = Decimal("0")
    low_stock_variants: int = 0
    pending_reviews: int = 0
    new_leads_period: dict = {}    # {newsletter, pos, contact}


class OrderExport(BaseModel):
    """Order export item for CSV."""
    order_number: str
    status: str
    customer_email: str
    customer_name: str
    shipping_address: str
    subtotal: Decimal
    shipping_cost: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    payment_method: Optional[str]
    tracking_number: Optional[str]
    created_at: datetime
    paid_at: Optional[datetime]
    shipped_at: Optional[datetime]
    delivered_at: Optional[datetime]
    items: str  # Formatted string: "Product1 x2, Product2 x1"


class ProductStats(BaseModel):
    """Product statistics for admin."""
    id: int
    name: str
    sku: Optional[str]
    stock: int
    is_low_stock: bool
    total_sold: int
    total_revenue: Decimal
    average_rating: Optional[float]
    review_count: int


class CustomerStats(BaseModel):
    """Customer statistics."""
    total_customers: int
    new_customers_period: int
    returning_customers: int
    average_orders_per_customer: float


class AdminUserItem(BaseModel):
    """User row for the admin users list."""
    id: int
    email: str
    first_name: str
    last_name: str
    phone: Optional[str]
    role: str
    is_active: bool
    email_verified: bool
    marketing_opt_in: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
    login_count: int = 0
    total_orders: int
    total_spent: Decimal
    order_ids: List[int]


class AdminUserUpdate(BaseModel):
    """Fields an admin can edit on any account."""
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    role: Optional[str] = Field(None, pattern="^(customer|admin)$")
    is_active: Optional[bool] = None
    email_verified: Optional[bool] = None
    marketing_opt_in: Optional[bool] = None


class AdminUserStatusUpdate(BaseModel):
    is_active: bool


class AdminSetPassword(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=100)
    notify_user: bool = True

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class AdminUserDetail(BaseModel):
    """Full customer profile (360º view) for the admin panel."""
    id: int
    email: str
    first_name: str
    last_name: str
    phone: Optional[str]
    role: str
    is_active: bool
    email_verified: bool
    marketing_opt_in: bool
    has_password: bool
    google_linked: bool
    failed_login_attempts: int
    locked_until: Optional[datetime]
    last_login_at: Optional[datetime]
    login_count: int
    created_at: datetime
    updated_at: Optional[datetime]
    stats: dict
    addresses: List[dict]
    orders: List[dict]
    coupon_redemptions: List[dict]
    reviews: List[dict]
    cart: Optional[dict]
    newsletter_lead: Optional[dict]


class AdminOrderNotes(BaseModel):
    admin_notes: Optional[str] = Field(None, max_length=5000)


class AdminOrderResponse(OrderResponse):
    """Order as seen by the admin panel (adds internal fields)."""
    user_id: Optional[int] = None
    guest_email: Optional[str] = None
    customer_name: Optional[str] = None
    admin_notes: Optional[str] = None
    payment_intent_id: Optional[str] = None
    shipping_status: Optional[str] = None
    updated_at: Optional[datetime] = None
