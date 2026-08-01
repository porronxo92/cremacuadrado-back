"""
Admin API endpoints — Product categories, image gallery, variants and
nutrition facts. Complements the core product CRUD in admin.py.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from slugify import slugify
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, AdminUser
from app.api.v1.admin import _variant_resp
from app.models.product import Category, Product, ProductImage, ProductNutrition, ProductVariant
from app.schemas.common import Message
from app.schemas.product import (
    CategoryCreate, CategoryResponse, CategoryUpdate,
    ProductImageCreate, ProductImageResponse, ProductImageReorderItem, ProductImageUpdate,
    ProductNutritionResponse, ProductNutritionUpsert,
    ProductVariantCreate, ProductVariantResponse,
)
from app.services import blob_service

logger = logging.getLogger("cremacuadrado.admin")

router = APIRouter()


# =============================================================================
# Product Categories
# =============================================================================

@router.get("/categories", response_model=List[CategoryResponse])
def list_categories_admin(db: DbSession, admin_user: AdminUser):
    """List all product categories (admin — includes inactive)."""
    return db.query(Category).order_by(Category.sort_order, Category.name).all()


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(data: CategoryCreate, db: DbSession, admin_user: AdminUser):
    """Create a product category."""
    slug = slugify(data.slug or data.name)
    if db.query(Category).filter(Category.slug == slug).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una categoría con este slug")

    category = Category(
        slug=slug, name=data.name, description=data.description,
        image_url=data.image_url, sort_order=data.sort_order, parent_id=data.parent_id,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/categories/{category_id}", response_model=CategoryResponse)
def update_category(category_id: int, data: CategoryUpdate, db: DbSession, admin_user: AdminUser):
    """Update a product category."""
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    update_data = data.model_dump(exclude_unset=True, exclude={"slug"})
    for field, value in update_data.items():
        setattr(category, field, value)

    if data.slug is not None:
        new_slug = slugify(data.slug)
        if db.query(Category).filter(Category.slug == new_slug, Category.id != category_id).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una categoría con este slug")
        category.slug = new_slug

    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", response_model=Message)
def delete_category(category_id: int, db: DbSession, admin_user: AdminUser):
    """Delete a product category. Blocked while it still has products assigned."""
    category = db.query(Category).options(joinedload(Category.products)).filter(
        Category.id == category_id
    ).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    if category.products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar: hay productos asignados a esta categoría",
        )

    db.delete(category)
    db.commit()
    return Message(message="Categoría eliminada")


# =============================================================================
# Product Image Gallery
# =============================================================================

def _get_product_or_404(db, product_id: int) -> Product:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return product


@router.post(
    "/products/{product_id}/images",
    response_model=ProductImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_product_image(product_id: int, data: ProductImageCreate, db: DbSession, admin_user: AdminUser):
    """Add an image to a product's gallery (product-level or a specific variant)."""
    _get_product_or_404(db, product_id)

    if data.variant_id is not None:
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == data.variant_id, ProductVariant.product_id == product_id,
        ).first()
        if not variant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variante no encontrada")

    if data.is_primary:
        db.query(ProductImage).filter(
            ProductImage.product_id == product_id, ProductImage.variant_id == data.variant_id,
        ).update({"is_primary": False})

    image = ProductImage(
        product_id=product_id,
        variant_id=data.variant_id,
        url=data.url,
        alt_text=data.alt_text,
        sort_order=data.sort_order,
        is_primary=data.is_primary,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return image


@router.put("/products/{product_id}/images/{image_id}", response_model=ProductImageResponse)
def update_product_image(
    product_id: int, image_id: int, data: ProductImageUpdate, db: DbSession, admin_user: AdminUser,
):
    """Update an image's alt text, order or primary flag."""
    image = db.query(ProductImage).filter(
        ProductImage.id == image_id, ProductImage.product_id == product_id,
    ).first()
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Imagen no encontrada")

    if data.is_primary:
        db.query(ProductImage).filter(
            ProductImage.product_id == product_id,
            ProductImage.variant_id == image.variant_id,
            ProductImage.id != image_id,
        ).update({"is_primary": False})

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(image, field, value)

    db.commit()
    db.refresh(image)
    return image


@router.post("/products/{product_id}/images/reorder", response_model=Message)
def reorder_product_images(
    product_id: int, items: List[ProductImageReorderItem], db: DbSession, admin_user: AdminUser,
):
    """Bulk-update sort_order for a product's gallery images."""
    _get_product_or_404(db, product_id)

    ids = [item.id for item in items]
    images = {img.id: img for img in db.query(ProductImage).filter(
        ProductImage.id.in_(ids), ProductImage.product_id == product_id,
    ).all()}

    for item in items:
        if item.id in images:
            images[item.id].sort_order = item.sort_order

    db.commit()
    return Message(message="Orden actualizado")


@router.delete("/products/{product_id}/images/{image_id}", response_model=Message)
async def delete_product_image(product_id: int, image_id: int, db: DbSession, admin_user: AdminUser):
    """Remove an image from a product's gallery and best-effort delete it from Blob storage."""
    image = db.query(ProductImage).filter(
        ProductImage.id == image_id, ProductImage.product_id == product_id,
    ).first()
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Imagen no encontrada")

    url = image.url
    db.delete(image)
    db.commit()

    try:
        await blob_service.delete(url)
    except Exception as exc:
        logger.warning("Could not delete blob for image %d (url=%s): %s", image_id, url, exc)

    return Message(message="Imagen eliminada")


# =============================================================================
# Product Variants
# =============================================================================

@router.post(
    "/products/{product_id}/variants",
    response_model=ProductVariantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_variant(product_id: int, data: ProductVariantCreate, db: DbSession, admin_user: AdminUser):
    """Add a new format/variant (e.g. 100g, 200g, 1kg) to a product."""
    _get_product_or_404(db, product_id)

    if data.sku and db.query(ProductVariant).filter(ProductVariant.sku == data.sku).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una variante con este SKU")

    variant = ProductVariant(
        product_id=product_id,
        sku=data.sku,
        format=data.format,
        weight_grams=data.weight_grams,
        price=data.price,
        compare_price=data.compare_price,
        stock=data.stock,
        is_active=data.is_active,
        sort_order=data.sort_order,
    )
    db.add(variant)
    db.flush()

    if data.image_url:
        db.add(ProductImage(
            product_id=product_id,
            variant_id=variant.id,
            url=data.image_url,
            is_primary=True,
            sort_order=0,
        ))

    db.commit()
    db.refresh(variant)
    return _variant_resp(variant)


@router.delete("/products/{product_id}/variants/{variant_id}", response_model=Message)
def delete_variant(product_id: int, variant_id: int, db: DbSession, admin_user: AdminUser):
    """Delete a variant. Blocked if it already has orders/cart references — deactivate it instead."""
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id, ProductVariant.product_id == product_id,
    ).first()
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variante no encontrada")

    db.delete(variant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede eliminar: la variante tiene pedidos o carritos asociados. Desactívala en su lugar.",
        )

    return Message(message="Variante eliminada")


# =============================================================================
# Product Nutrition
# =============================================================================

@router.put("/products/{product_id}/nutrition", response_model=ProductNutritionResponse)
def upsert_nutrition(product_id: int, data: ProductNutritionUpsert, db: DbSession, admin_user: AdminUser):
    """Create or update a product's nutrition facts (per 100g)."""
    _get_product_or_404(db, product_id)

    nutrition = db.query(ProductNutrition).filter(ProductNutrition.product_id == product_id).first()
    if not nutrition:
        nutrition = ProductNutrition(product_id=product_id)
        db.add(nutrition)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(nutrition, field, value)

    db.commit()
    db.refresh(nutrition)
    return nutrition
