"""Admin-related schemas."""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel


class DashboardStats(BaseModel):
    """Dashboard statistics."""
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
