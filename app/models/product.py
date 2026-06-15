"""
Product models - Categories, Products, ProductVariants, Images, Nutrition, Reviews.
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
    """Product model — master product (e.g. Crema Pura, Crema Crunchy)."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(50), unique=True, nullable=True, index=True)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    short_description = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    badge_color = Column(String(20), nullable=True)  # e.g. #F5A542 (Crunchy), #A2BA1C (Pura)
    # price/stock/weight live on ProductVariant — kept nullable for legacy compat
    price = Column(Numeric(10, 2), nullable=True)
    compare_price = Column(Numeric(10, 2), nullable=True)
    cost = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0, nullable=True)
    low_stock_threshold = Column(Integer, default=5, nullable=False)
    weight = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    meta_title = Column(String(200), nullable=True)
    meta_description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    category = relationship("Category", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan", order_by="ProductVariant.sort_order")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.sort_order")
    nutrition = relationship("ProductNutrition", back_populates="product", uselist=False, cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="product", cascade="all, delete-orphan")
    cart_items = relationship("CartItem", back_populates="product")
    order_items = relationship("OrderItem", back_populates="product")

    @property
    def primary_image(self) -> str | None:
        product_imgs = [img for img in self.images if img.variant_id is None]
        for img in product_imgs:
            if img.is_primary:
                return img.url
        return product_imgs[0].url if product_imgs else None

    @property
    def is_in_stock(self) -> bool:
        return any(v.is_in_stock for v in self.variants if v.is_active)

    @property
    def min_price(self) -> Decimal | None:
        active = [v.price for v in self.variants if v.is_active]
        return min(active) if active else None

    @property
    def average_rating(self) -> float | None:
        approved = [r for r in self.reviews if r.status == "approved"]
        if not approved:
            return None
        return sum(r.rating for r in approved) / len(approved)

    @property
    def review_count(self) -> int:
        return len([r for r in self.reviews if r.status == "approved"])

    def __repr__(self):
        return f"<Product {self.name}>"


class ProductVariant(Base):
    """Product variant — one per format (100g, 200g, 1kg)."""
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sku = Column(String(50), unique=True, nullable=True, index=True)
    format = Column(String(20), nullable=False)  # '100g', '200g', '1kg'
    weight_grams = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    compare_price = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0, nullable=False)
    low_stock_threshold = Column(Integer, default=5, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint('price >= 0', name='check_variant_price_positive'),
        CheckConstraint('stock >= 0', name='check_variant_stock_positive'),
    )

    # Relationships
    product = relationship("Product", back_populates="variants")
    images = relationship(
        "ProductImage",
        back_populates="variant",
        order_by="ProductImage.sort_order",
        foreign_keys="ProductImage.variant_id",
    )
    cart_items = relationship("CartItem", back_populates="variant")
    order_items = relationship("OrderItem", back_populates="variant")

    @property
    def is_in_stock(self) -> bool:
        return self.stock > 0

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.stock <= self.low_stock_threshold

    def __repr__(self):
        return f"<ProductVariant {self.sku} {self.format}>"


class ProductImage(Base):
    """Product image model."""
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True)
    url = Column(String(500), nullable=False)
    alt_text = Column(String(200), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)

    # Relationships
    product = relationship("Product", back_populates="images")
    variant = relationship("ProductVariant", back_populates="images", foreign_keys=[variant_id])

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
