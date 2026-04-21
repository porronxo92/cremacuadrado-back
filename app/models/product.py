"""
Product models - Categories, Products, Images, Nutrition, Reviews.
"""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, Integer, String, Text, Numeric, Boolean, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship

from app.models.database import Base


class Category(Base):
    """Product category model."""
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    image_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    parent = relationship("Category", remote_side=[id], backref="subcategories")
    products = relationship("Product", back_populates="category")
    
    def __repr__(self):
        return f"<Category {self.name}>"


class Product(Base):
    """Product model."""
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(50), unique=True, nullable=True, index=True)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    short_description = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    compare_price = Column(Numeric(10, 2), nullable=True)  # Original price for discounts
    cost = Column(Numeric(10, 2), nullable=True)  # Cost price
    stock = Column(Integer, default=0, nullable=False)
    low_stock_threshold = Column(Integer, default=5, nullable=False)
    weight = Column(Integer, nullable=True)  # Weight in grams
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    meta_title = Column(String(200), nullable=True)
    meta_description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint('price >= 0', name='check_price_positive'),
        CheckConstraint('stock >= 0', name='check_stock_positive'),
    )
    
    # Relationships
    category = relationship("Category", back_populates="products")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.sort_order")
    nutrition = relationship("ProductNutrition", back_populates="product", uselist=False, cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="product", cascade="all, delete-orphan")
    cart_items = relationship("CartItem", back_populates="product")
    order_items = relationship("OrderItem", back_populates="product")
    
    @property
    def primary_image(self) -> str | None:
        """Get primary image URL."""
        for img in self.images:
            if img.is_primary:
                return img.url
        return self.images[0].url if self.images else None
    
    @property
    def is_in_stock(self) -> bool:
        """Check if product is in stock."""
        return self.stock > 0
    
    @property
    def is_low_stock(self) -> bool:
        """Check if product has low stock."""
        return 0 < self.stock <= self.low_stock_threshold
    
    @property
    def average_rating(self) -> float | None:
        """Calculate average rating from approved reviews."""
        approved_reviews = [r for r in self.reviews if r.status == "approved"]
        if not approved_reviews:
            return None
        return sum(r.rating for r in approved_reviews) / len(approved_reviews)
    
    @property
    def review_count(self) -> int:
        """Count approved reviews."""
        return len([r for r in self.reviews if r.status == "approved"])
    
    def __repr__(self):
        return f"<Product {self.name}>"


class ProductImage(Base):
    """Product image model."""
    __tablename__ = "product_images"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    url = Column(String(500), nullable=False)
    alt_text = Column(String(200), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    
    # Relationships
    product = relationship("Product", back_populates="images")
    
    def __repr__(self):
        return f"<ProductImage {self.id} for Product {self.product_id}>"


class ProductNutrition(Base):
    """Product nutrition information (per 100g)."""
    __tablename__ = "product_nutrition"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, unique=True)
    energy_kcal = Column(Numeric(10, 2), nullable=True)  # Calorías
    energy_kj = Column(Numeric(10, 2), nullable=True)
    fat = Column(Numeric(10, 2), nullable=True)  # Grasas totales
    saturated_fat = Column(Numeric(10, 2), nullable=True)  # Grasas saturadas
    carbohydrates = Column(Numeric(10, 2), nullable=True)  # Carbohidratos
    sugars = Column(Numeric(10, 2), nullable=True)  # Azúcares
    fiber = Column(Numeric(10, 2), nullable=True)  # Fibra
    proteins = Column(Numeric(10, 2), nullable=True)  # Proteínas
    salt = Column(Numeric(10, 2), nullable=True)  # Sal
    
    # Relationships
    product = relationship("Product", back_populates="nutrition")
    
    def __repr__(self):
        return f"<ProductNutrition for Product {self.product_id}>"


class Review(Base):
    """Product review model."""
    __tablename__ = "reviews"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rating = Column(Integer, nullable=False)  # 1-5
    title = Column(String(200), nullable=True)
    comment = Column(Text, nullable=True)
    is_verified_purchase = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending, approved, rejected
    admin_response = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint('rating >= 1 AND rating <= 5', name='check_rating_range'),
    )
    
    # Relationships
    product = relationship("Product", back_populates="reviews")
    user = relationship("User", back_populates="reviews")
    
    def __repr__(self):
        return f"<Review {self.rating}★ for Product {self.product_id}>"
