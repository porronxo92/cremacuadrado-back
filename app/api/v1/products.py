"""
Products API endpoints.
"""
from typing import Optional, List
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func

from app.api.deps import DbSession, CurrentUser, CurrentUserOptional
from app.models.product import Product, Category, Review
from app.models.order import OrderItem
from app.schemas.product import (
    ProductResponse, ProductListResponse, CategoryResponse,
    ReviewCreate, ReviewResponse
)
from app.schemas.common import PaginatedResponse
from app.config import settings

router = APIRouter()


def product_to_list_response(product: Product) -> ProductListResponse:
    """Convert Product model to ProductListResponse."""
    return ProductListResponse(
        id=product.id,
        sku=product.sku,
        slug=product.slug,
        name=product.name,
        short_description=product.short_description,
        price=product.price,
        compare_price=product.compare_price,
        is_in_stock=product.is_in_stock,
        is_low_stock=product.is_low_stock,
        is_featured=product.is_featured,
        primary_image=product.primary_image,
        category_slug=product.category.slug if product.category else None,
        average_rating=product.average_rating,
        review_count=product.review_count,
    )


@router.get("", response_model=PaginatedResponse[ProductListResponse])
async def list_products(
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    in_stock: Optional[bool] = None,
    featured: Optional[bool] = None,
    sort_by: str = Query("created_at", pattern="^(name|price|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    List products with filters and pagination.
    
    Filters:
    - category: Filter by category slug
    - search: Search in name and description
    - min_price/max_price: Price range
    - in_stock: Only products in stock
    - featured: Only featured products
    """
    query = db.query(Product).options(
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.reviews),
    ).filter(Product.is_active == True)
    
    # Category filter
    if category:
        query = query.join(Category).filter(Category.slug == category)
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Product.name.ilike(search_term),
                Product.short_description.ilike(search_term),
                Product.description.ilike(search_term),
            )
        )
    
    # Price filters
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)
    
    # Stock filter
    if in_stock is True:
        query = query.filter(Product.stock > 0)
    
    # Featured filter
    if featured is True:
        query = query.filter(Product.is_featured == True)
    
    # Sorting
    sort_column = getattr(Product, sort_by)
    if sort_order == "desc":
        sort_column = sort_column.desc()
    query = query.order_by(sort_column)
    
    # Count total
    total = query.count()
    
    # Paginate
    offset = (page - 1) * page_size
    products = query.offset(offset).limit(page_size).all()
    
    # Convert to response
    items = [product_to_list_response(p) for p in products]
    
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/categories", response_model=List[CategoryResponse])
async def list_categories(db: DbSession, include_empty: bool = False):
    """List all active product categories."""
    query = db.query(Category).filter(Category.is_active == True)
    
    if not include_empty:
        # Only categories with products
        query = query.join(Product).filter(Product.is_active == True).distinct()
    
    categories = query.order_by(Category.sort_order, Category.name).all()
    return categories


@router.get("/featured", response_model=List[ProductListResponse])
async def list_featured_products(db: DbSession, limit: int = Query(4, ge=1, le=10)):
    """Get featured products for homepage."""
    products = db.query(Product).options(
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.reviews),
    ).filter(
        Product.is_active == True,
        Product.is_featured == True,
    ).limit(limit).all()
    
    return [product_to_list_response(p) for p in products]


@router.get("/{slug}", response_model=ProductResponse)
async def get_product(slug: str, db: DbSession):
    """Get product details by slug."""
    product = db.query(Product).options(
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.nutrition),
        joinedload(Product.reviews).joinedload(Review.user),
    ).filter(
        Product.slug == slug,
        Product.is_active == True,
    ).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )
    
    return ProductResponse(
        id=product.id,
        sku=product.sku,
        slug=product.slug,
        name=product.name,
        short_description=product.short_description,
        description=product.description,
        price=product.price,
        compare_price=product.compare_price,
        stock=product.stock,
        weight=product.weight,
        is_active=product.is_active,
        is_featured=product.is_featured,
        is_in_stock=product.is_in_stock,
        is_low_stock=product.is_low_stock,
        category=product.category,
        images=product.images,
        nutrition=product.nutrition,
        average_rating=product.average_rating,
        review_count=product.review_count,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get("/{slug}/reviews", response_model=List[ReviewResponse])
async def get_product_reviews(slug: str, db: DbSession):
    """Get approved reviews for a product."""
    product = db.query(Product).filter(Product.slug == slug).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )
    
    reviews = db.query(Review).options(
        joinedload(Review.user)
    ).filter(
        Review.product_id == product.id,
        Review.status == "approved"
    ).order_by(Review.created_at.desc()).all()
    
    return [
        ReviewResponse(
            id=r.id,
            rating=r.rating,
            title=r.title,
            comment=r.comment,
            user_id=r.user_id,
            user_name=r.user.first_name if r.user else "Anónimo",
            is_verified_purchase=r.is_verified_purchase,
            status=r.status,
            created_at=r.created_at,
        )
        for r in reviews
    ]


@router.post("/{slug}/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_review(
    slug: str,
    review_data: ReviewCreate,
    db: DbSession,
    current_user: CurrentUser
):
    """Create a review for a product (authenticated users only)."""
    product = db.query(Product).filter(Product.slug == slug).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )
    
    # Check if user already reviewed this product
    existing_review = db.query(Review).filter(
        Review.product_id == product.id,
        Review.user_id == current_user.id
    ).first()
    
    if existing_review:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya has dejado una reseña para este producto"
        )
    
    # Check if user has purchased this product
    has_purchased = db.query(OrderItem).join(OrderItem.order).filter(
        OrderItem.product_id == product.id,
        OrderItem.order.has(user_id=current_user.id),
        OrderItem.order.has(status="delivered")
    ).first() is not None
    
    # Create review
    review = Review(
        product_id=product.id,
        user_id=current_user.id,
        rating=review_data.rating,
        title=review_data.title,
        comment=review_data.comment,
        is_verified_purchase=has_purchased,
        status="pending",  # Reviews need approval
    )
    
    db.add(review)
    db.commit()
    db.refresh(review)
    
    return ReviewResponse(
        id=review.id,
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        user_id=review.user_id,
        user_name=current_user.first_name,
        is_verified_purchase=review.is_verified_purchase,
        status=review.status,
        created_at=review.created_at,
    )
