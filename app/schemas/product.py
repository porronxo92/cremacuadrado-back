"""Product-related schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


# =============================================================================
# Category schemas
# =============================================================================

class CategoryBase(BaseModel):
    """Base category schema."""
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: int = 0


class CategoryCreate(CategoryBase):
    """Category creation schema."""
    parent_id: Optional[int] = None


class CategoryResponse(CategoryBase):
    """Category response schema."""
    id: int
    parent_id: Optional[int]
    is_active: bool
    
    model_config = {"from_attributes": True}


# =============================================================================
# Product Image schemas
# =============================================================================

class ProductImageResponse(BaseModel):
    """Product image response schema."""
    id: int
    url: str
    alt_text: Optional[str]
    sort_order: int
    is_primary: bool
    
    model_config = {"from_attributes": True}


# =============================================================================
# Product Nutrition schemas
# =============================================================================

class ProductNutritionResponse(BaseModel):
    """Product nutrition response schema (per 100g)."""
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
# Review schemas
# =============================================================================

class ReviewBase(BaseModel):
    """Base review schema."""
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str] = Field(None, max_length=200)
    comment: Optional[str] = None


class ReviewCreate(ReviewBase):
    """Review creation schema."""
    pass


class ReviewResponse(ReviewBase):
    """Review response schema."""
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

class ProductBase(BaseModel):
    """Base product schema."""
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    sku: Optional[str] = Field(None, max_length=50)
    short_description: Optional[str] = None
    description: Optional[str] = None
    price: Decimal = Field(..., ge=0)
    compare_price: Optional[Decimal] = Field(None, ge=0)
    stock: int = Field(default=0, ge=0)
    weight: Optional[int] = None
    is_active: bool = True
    is_featured: bool = False
    category_id: Optional[int] = None


class ProductCreate(ProductBase):
    """Product creation schema."""
    pass


class ProductUpdate(BaseModel):
    """Product update schema."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    slug: Optional[str] = Field(None, min_length=1, max_length=200)
    sku: Optional[str] = Field(None, max_length=50)
    short_description: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, ge=0)
    compare_price: Optional[Decimal] = Field(None, ge=0)
    stock: Optional[int] = Field(None, ge=0)
    weight: Optional[int] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    category_id: Optional[int] = None


class ProductResponse(BaseModel):
    """Product response schema."""
    id: int
    sku: Optional[str]
    slug: str
    name: str
    short_description: Optional[str]
    description: Optional[str]
    price: Decimal
    compare_price: Optional[Decimal]
    stock: int
    weight: Optional[int]
    is_active: bool
    is_featured: bool
    is_in_stock: bool
    is_low_stock: bool
    category: Optional[CategoryResponse]
    images: List[ProductImageResponse]
    nutrition: Optional[ProductNutritionResponse]
    average_rating: Optional[float]
    review_count: int
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    """Product list item schema (lighter than full response)."""
    id: int
    sku: Optional[str]
    slug: str
    name: str
    short_description: Optional[str]
    price: Decimal
    compare_price: Optional[Decimal]
    is_in_stock: bool
    is_low_stock: bool
    is_featured: bool
    primary_image: Optional[str]
    category_slug: Optional[str]
    average_rating: Optional[float]
    review_count: int
    
    model_config = {"from_attributes": True}
