"""
Order models - Orders, OrderItems, Coupons.
"""
from datetime import datetime
from decimal import Decimal
import json
from sqlalchemy import Column, Integer, String, Text, Numeric, Boolean, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship

from app.models.database import Base


class Order(Base):
    """Order model."""
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    order_number = Column(String(20), unique=True, nullable=False, index=True)
    status = Column(String(30), default="pending", nullable=False)
    # Status flow: pending -> paid -> processing -> shipped -> delivered
    # Also: cancelled, refunded
    
    # Pricing
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), default=0, nullable=False)
    discount = Column(Numeric(10, 2), default=0, nullable=False)
    tax = Column(Numeric(10, 2), default=0, nullable=False)
    total = Column(Numeric(10, 2), nullable=False)
    
    # Coupon
    coupon_code = Column(String(50), nullable=True)
    
    # Addresses (stored as JSON for historical record)
    shipping_address_json = Column(Text, nullable=False)
    billing_address_json = Column(Text, nullable=True)  # If different from shipping
    
    # Payment
    payment_method = Column(String(50), nullable=True)  # card, bizum, etc.
    payment_intent_id = Column(String(255), nullable=True)  # Stripe payment intent
    paid_at = Column(DateTime, nullable=True)
    
    # Shipping
    tracking_number = Column(String(100), nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    
    # Guest info (if no user_id)
    guest_email = Column(String(255), nullable=True)
    
    # Notes
    customer_notes = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    
    @property
    def shipping_address(self) -> dict:
        """Get shipping address as dict."""
        return json.loads(self.shipping_address_json) if self.shipping_address_json else {}
    
    @shipping_address.setter
    def shipping_address(self, value: dict):
        """Set shipping address from dict."""
        self.shipping_address_json = json.dumps(value)
    
    @property
    def billing_address(self) -> dict | None:
        """Get billing address as dict."""
        return json.loads(self.billing_address_json) if self.billing_address_json else None
    
    @billing_address.setter
    def billing_address(self, value: dict | None):
        """Set billing address from dict."""
        self.billing_address_json = json.dumps(value) if value else None
    
    @property
    def customer_email(self) -> str:
        """Get customer email (from user or guest)."""
        if self.user:
            return self.user.email
        return self.guest_email
    
    @property
    def item_count(self) -> int:
        """Get total number of items."""
        return sum(item.quantity for item in self.items)
    
    @staticmethod
    def generate_order_number() -> str:
        """Generate unique order number."""
        import random
        import string
        timestamp = datetime.utcnow().strftime("%y%m%d")
        random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"CC-{timestamp}-{random_part}"
    
    def __repr__(self):
        return f"<Order {self.order_number}>"


class OrderItem(Base):
    """Order item model."""
    __tablename__ = "order_items"
    
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    
    # Snapshot of product at time of order
    product_name = Column(String(200), nullable=False)
    product_sku = Column(String(50), nullable=True)
    product_image_url = Column(String(500), nullable=True)
    
    # Pricing
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(10, 2), nullable=False)
    
    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")
    
    def __repr__(self):
        return f"<OrderItem {self.product_name} x{self.quantity}>"


class Coupon(Base):
    """Discount coupon model."""
    __tablename__ = "coupons"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)
    discount_type = Column(String(20), nullable=False)  # percent, fixed
    discount_value = Column(Numeric(10, 2), nullable=False)
    min_order_amount = Column(Numeric(10, 2), default=0, nullable=False)
    max_discount_amount = Column(Numeric(10, 2), nullable=True)  # For percent discounts
    usage_limit = Column(Integer, nullable=True)  # Null = unlimited
    used_count = Column(Integer, default=0, nullable=False)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint("discount_type IN ('percent', 'fixed')", name='check_discount_type'),
        CheckConstraint('discount_value > 0', name='check_discount_positive'),
    )
    
    @property
    def is_valid(self) -> bool:
        """Check if coupon is currently valid."""
        if not self.is_active:
            return False
        
        now = datetime.utcnow()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False
        
        return True
    
    def calculate_discount(self, subtotal: Decimal) -> Decimal:
        """Calculate discount amount for a given subtotal."""
        if subtotal < self.min_order_amount:
            return Decimal('0')
        
        if self.discount_type == 'percent':
            discount = subtotal * (self.discount_value / 100)
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
        else:  # fixed
            discount = min(self.discount_value, subtotal)
        
        return discount
    
    def __repr__(self):
        return f"<Coupon {self.code}>"
