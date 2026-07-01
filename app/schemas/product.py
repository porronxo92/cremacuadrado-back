"""Product-related schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Category schemas
# =============================================================================

class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: int = 0


class CategoryCreate(CategoryBase):
    parent_id: Optional[int] = None


class CategoryResponse(CategoryBase):
    id: int
    parent_id: Optional[int]
    is_active: bool

    model_config = {"from_attributes": True}


# =============================================================================
# Product Image schemas
# =============================================================================

class ProductImageResponse(BaseModel):
    id: int
    url: str
    alt_text: Optional[str]
    sort_order: int
    is_primary: bool

    model_config = {"from_attributes": True}

    @field_validator('url', mode='before')
    @classmethod
    def normalize_url(cls, v: str) -> str:
        """Normalize image URLs to use correct paths."""
        from app.utils.url import normalize_image_url
        return normalize_image_url(v) or v


# =============================================================================
# Product Nutrition schemas
# =============================================================================

class ProductNutritionResponse(BaseModel):
    energy_kcal: Optional[Decimal]
    energy_kj: Optional[Decimal]
    fat: Optional[Decimal]
    saturated_fat: Optional[Decimal]
    carbohydrates: Optional[Decimal]
    sugars: Optional[Decimal]
    fiber: Optional[Decimal]
    proteins: Optional[Decimal]
    salt: Optional[Decimal]

    model_config = {"from_attributes": True}


# =============================================================================
# Product Variant schemas
# =============================================================================

class ProductVariantResponse(BaseModel):
    """One format of a product: 100g, 200g or 1kg."""
    id: int
    sku: Optional[str]
    format: str          # '100g', '200g', '1kg'
    weight_grams: int
    price: Decimal
    compare_price: Optional[Decimal]
    stock: int
    is_active: bool
    is_in_stock: bool
    is_low_stock: bool
    sort_order: int
    images: List[ProductImageResponse] = []

    model_config = {"from_attributes": True}


# =============================================================================
# Review schemas
# =============================================================================

class ReviewBase(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str] = Field(None, max_length=200)
    comment: Optional[str] = None


class ReviewCreate(ReviewBase):
    pass


class ReviewResponse(ReviewBase):
    id: int
    user_id: Optional[int]
    user_name: Optional[str] = None
    is_verified_purchase: bool
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Product schemas
# =============================================================================

class ProductResponse(BaseModel):
    """Full product detail — includes all variants."""
    id: int
    sku: Optional[str]
    slug: str
    name: str
    short_description: Optional[str]
    description: Optional[str]
    badge_color: Optional[str]
    audio_url: Optional[str] = None
    is_active: bool
    is_featured: bool
    is_in_stock: bool
    category: Optional[CategoryResponse]
    images: List[ProductImageResponse]
    nutrition: Optional[ProductNutritionResponse]
    variants: List[ProductVariantResponse]
    average_rating: Optional[float]
    review_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    """Product card for catalog — lighter payload."""
    id: int
    sku: Optional[str]
    slug: str
    name: str
    short_description: Optional[str]
    badge_color: Optional[str]
    is_in_stock: bool
    is_featured: bool
    primary_image: Optional[str]
    category_slug: Optional[str]
    variants: List[ProductVariantResponse]
    average_rating: Optional[float]
    review_count: int

    model_config = {"from_attributes": True}
