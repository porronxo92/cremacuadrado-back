"""
Admin API endpoints - Dashboard, Order Management, Product CRUD.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
import csv
import io

import logging
import os
import re
import uuid as _uuid

logger = logging.getLogger("cremacuadrado.admin")

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_

from app.api.deps import DbSession, AdminUser
from app.models.user import User
from app.models.product import Product, Category, Review, ProductVariant, ProductImage
from app.models.order import Order, OrderItem
from app.models.shipment import Shipment
from app.schemas.order import OrderResponse, OrderStatusUpdate
from app.schemas.product import ProductResponse, ProductVariantResponse
from app.schemas.admin import DashboardStats
from app.schemas.common import Message, PaginatedResponse
from app.services.email import EmailService
from app.services import blob_service
from app.config import settings

router = APIRouter()

VALID_ORDER_STATUSES = {
    "pending_payment", "payment_failed", "paid",
    "processing", "shipped", "delivered", "cancelled", "refunded",
}

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}


def _safe_dest(dest_path: str) -> str:
    """Sanitize dest_path and reject traversal attempts."""
    clean = re.sub(r"[^a-zA-Z0-9 _\-/.]", "", dest_path).strip("/")
    if ".." in clean:
        raise HTTPException(status_code=400, detail="Ruta no permitida")
    return clean


def _variant_resp(v: ProductVariant) -> ProductVariantResponse:
    return ProductVariantResponse(
        id=v.id, sku=v.sku, format=v.format, weight_grams=v.weight_grams,
        price=v.price, compare_price=v.compare_price, stock=v.stock,
        is_active=v.is_active, is_in_stock=v.is_in_stock, is_low_stock=v.is_low_stock,
        sort_order=v.sort_order,
        images=v.images,
    )


def _product_response(product: Product) -> ProductResponse:
    product_level_images = [img for img in product.images if img.variant_id is None]
    return ProductResponse(
        id=product.id,
        sku=product.sku,
        slug=product.slug,
        name=product.name,
        short_description=product.short_description,
        description=product.description,
        badge_color=product.badge_color,
        audio_url=product.audio_url,
        is_active=product.is_active,
        is_featured=product.is_featured,
        is_in_stock=product.is_in_stock,
        category=product.category,
        images=product_level_images,
        nutrition=product.nutrition,
        variants=[_variant_resp(v) for v in product.variants],
        average_rating=product.average_rating,
        review_count=product.review_count,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


# =============================================================================
# Dashboard
# =============================================================================

@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard(db: DbSession, admin_user: AdminUser):
    """Get dashboard statistics."""
    today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(today, datetime.min.time())
    period_start = today_start - timedelta(days=30)
    prev_period_start = period_start - timedelta(days=30)
    
    # Today's orders
    orders_today = db.query(func.count(Order.id)).filter(
        Order.created_at >= today_start,
        Order.status != 'cancelled'
    ).scalar() or 0
    
    revenue_today = db.query(func.sum(Order.total)).filter(
        Order.created_at >= today_start,
        Order.status.in_(['paid', 'processing', 'shipped', 'delivered'])
    ).scalar() or Decimal('0')
    
    # Period orders (30 days)
    orders_period = db.query(func.count(Order.id)).filter(
        Order.created_at >= period_start,
        Order.status != 'cancelled'
    ).scalar() or 0
    
    revenue_period = db.query(func.sum(Order.total)).filter(
        Order.created_at >= period_start,
        Order.status.in_(['paid', 'processing', 'shipped', 'delivered'])
    ).scalar() or Decimal('0')
    
    # Previous period for comparison
    orders_prev = db.query(func.count(Order.id)).filter(
        and_(Order.created_at >= prev_period_start, Order.created_at < period_start),
        Order.status != 'cancelled'
    ).scalar() or 0
    
    revenue_prev = db.query(func.sum(Order.total)).filter(
        and_(Order.created_at >= prev_period_start, Order.created_at < period_start),
        Order.status.in_(['paid', 'processing', 'shipped', 'delivered'])
    ).scalar() or Decimal('0')
    
    # Growth calculations
    orders_growth = None
    if orders_prev > 0:
        orders_growth = ((orders_period - orders_prev) / orders_prev) * 100
    
    revenue_growth = None
    if revenue_prev > 0:
        revenue_growth = float((revenue_period - revenue_prev) / revenue_prev * 100)
    
    # Average order value
    avg_order_value = revenue_period / orders_period if orders_period > 0 else Decimal('0')
    
    # Top products
    top_products_query = db.query(
        Product.name,
        func.sum(OrderItem.quantity).label('quantity_sold'),
        func.sum(OrderItem.total).label('revenue')
    ).join(OrderItem).join(Order).filter(
        Order.created_at >= period_start,
        Order.status.in_(['paid', 'processing', 'shipped', 'delivered'])
    ).group_by(Product.id, Product.name).order_by(
        func.sum(OrderItem.total).desc()
    ).limit(5).all()
    
    top_products = [
        {
            "product_name": name,
            "quantity_sold": qty,
            "revenue": float(rev)
        }
        for name, qty, rev in top_products_query
    ]
    
    # Orders by status
    status_counts = db.query(
        Order.status,
        func.count(Order.id)
    ).filter(
        Order.created_at >= period_start
    ).group_by(Order.status).all()
    
    orders_by_status = {status: count for status, count in status_counts}
    
    return DashboardStats(
        orders_today=orders_today,
        revenue_today=revenue_today,
        orders_period=orders_period,
        revenue_period=revenue_period,
        average_order_value=avg_order_value,
        top_products=top_products,
        orders_by_status=orders_by_status,
        orders_growth=orders_growth,
        revenue_growth=revenue_growth,
    )


# =============================================================================
# Order Management
# =============================================================================

@router.get("/orders", response_model=PaginatedResponse[OrderResponse])
def list_all_orders(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
):
    """List all orders with filters (admin only)."""
    query = db.query(Order).options(
        joinedload(Order.items),
        joinedload(Order.user)
    )
    
    # Filters
    if status:
        query = query.filter(Order.status == status)
    
    if date_from:
        query = query.filter(Order.created_at >= date_from)
    
    if date_to:
        query = query.filter(Order.created_at <= date_to)
    
    if search:
        query = query.filter(
            Order.order_number.ilike(f"%{search}%") |
            Order.guest_email.ilike(f"%{search}%")
        )
    
    # Order by date
    query = query.order_by(Order.created_at.desc())
    
    # Count total
    total = query.count()
    
    # Paginate
    offset = (page - 1) * page_size
    orders = query.offset(offset).limit(page_size).all()
    
    items = [
        OrderResponse(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            discount=order.discount,
            coupon_code=order.coupon_code,
            tax=order.tax,
            total=order.total,
            shipping_address=order.shipping_address,
            billing_address=order.billing_address,
            payment_method=order.payment_method,
            tracking_number=order.tracking_number,
            customer_notes=order.customer_notes,
            items=order.items,
            item_count=order.item_count,
            created_at=order.created_at,
            paid_at=order.paid_at,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
        )
        for order in orders
    ]
    
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order_admin(order_id: int, db: DbSession, admin_user: AdminUser):
    """Get order details (admin)."""
    order = db.query(Order).options(
        joinedload(Order.items),
        joinedload(Order.user)
    ).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )
    
    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        shipping_cost=order.shipping_cost,
        discount=order.discount,
        coupon_code=order.coupon_code,
        tax=order.tax,
        total=order.total,
        shipping_address=order.shipping_address,
        billing_address=order.billing_address,
        payment_method=order.payment_method,
        tracking_number=order.tracking_number,
        customer_notes=order.customer_notes,
        items=order.items,
        item_count=order.item_count,
        created_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
    )


def _do_update_order_status(order_id: int, status_data: OrderStatusUpdate, db, admin_user):
    """Shared logic for PUT and PATCH on order status."""
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )

    if status_data.status not in VALID_ORDER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado no válido. Valores permitidos: {', '.join(sorted(VALID_ORDER_STATUSES))}"
        )

    old_status = order.status
    order.status = status_data.status
    
    # Update timestamps
    if status_data.status == "shipped":
        order.shipped_at = datetime.now(timezone.utc)
        if status_data.tracking_number:
            order.tracking_number = status_data.tracking_number
    elif status_data.status == "delivered":
        order.delivered_at = datetime.now(timezone.utc)
    
    if status_data.admin_notes:
        order.admin_notes = status_data.admin_notes

    db.commit()
    db.refresh(order)

    customer_email = order.customer_email
    customer_name = order.shipping_address.get("first_name", "Cliente")

    if old_status != status_data.status:
        # Notify customer
        if status_data.status == "shipped" and order.tracking_number:
            EmailService.send_order_shipped_email(
                to_email=customer_email,
                order_number=order.order_number,
                customer_name=customer_name,
                tracking_number=order.tracking_number,
            )
        elif customer_email:
            EmailService.send_order_status_update_email(
                to_email=customer_email,
                order_number=order.order_number,
                customer_name=customer_name,
                new_status=status_data.status,
            )

    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        shipping_cost=order.shipping_cost,
        discount=order.discount,
        coupon_code=order.coupon_code,
        tax=order.tax,
        total=order.total,
        shipping_address=order.shipping_address,
        billing_address=order.billing_address,
        payment_method=order.payment_method,
        tracking_number=order.tracking_number,
        customer_notes=order.customer_notes,
        items=order.items,
        item_count=order.item_count,
        created_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
    )


@router.put("/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status_put(order_id: int, status_data: OrderStatusUpdate, db: DbSession, admin_user: AdminUser):
    """Update order status (admin) — PUT."""
    return _do_update_order_status(order_id, status_data, db, admin_user)


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status_patch(order_id: int, status_data: OrderStatusUpdate, db: DbSession, admin_user: AdminUser):
    """Update order status (admin) — PATCH alias."""
    return _do_update_order_status(order_id, status_data, db, admin_user)


@router.get("/orders/{order_id}/shipment")
def get_order_shipment(order_id: int, db: DbSession, admin_user: AdminUser):
    """Get Correos shipment details for an order."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if not shipment:
        return {"shipment": None}

    correos_url = None
    if shipment.localizador:
        correos_url = (
            f"https://www.correos.es/es/es/herramientas/localizador/envios/detalle"
            f"?tracking-number={shipment.localizador}"
        )

    return {
        "shipment": {
            "id": shipment.id,
            "localizador": shipment.localizador,
            "status": shipment.status,
            "service_code": shipment.service_code,
            "weight_grams": shipment.weight_grams,
            "label_url": shipment.label_url,
            "error": shipment.error,
            "correos_tracking_url": correos_url,
            "created_at": shipment.created_at,
            "updated_at": shipment.updated_at,
        }
    }


@router.patch("/orders/{order_id}/tracking", response_model=OrderResponse)
def update_tracking_number(
    order_id: int,
    db: DbSession,
    admin_user: AdminUser,
    tracking_number: str = Query(..., description="Número de seguimiento Correos"),
):
    """Manually set or update the tracking number for an order."""
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    order.tracking_number = tracking_number

    # Also update the associated shipment localizador if one exists
    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if shipment:
        shipment.localizador = tracking_number

    db.commit()
    db.refresh(order)

    return OrderResponse(
        id=order.id, order_number=order.order_number, status=order.status,
        subtotal=order.subtotal, shipping_cost=order.shipping_cost,
        discount=order.discount, coupon_code=order.coupon_code,
        tax=order.tax, total=order.total,
        shipping_address=order.shipping_address, billing_address=order.billing_address,
        payment_method=order.payment_method, tracking_number=order.tracking_number,
        customer_notes=order.customer_notes, items=order.items,
        item_count=order.item_count, created_at=order.created_at,
        paid_at=order.paid_at, shipped_at=order.shipped_at, delivered_at=order.delivered_at,
    )


@router.get("/orders/export/csv")
def export_orders_csv(
    db: DbSession,
    admin_user: AdminUser,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
):
    """Export orders to CSV."""
    query = db.query(Order).options(
        joinedload(Order.items),
        joinedload(Order.user)
    )
    
    if status:
        query = query.filter(Order.status == status)
    if date_from:
        query = query.filter(Order.created_at >= date_from)
    if date_to:
        query = query.filter(Order.created_at <= date_to)
    
    orders = query.order_by(Order.created_at.desc()).all()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        "Nº Pedido", "Estado", "Email", "Cliente", "Dirección",
        "Subtotal", "Envío", "Descuento", "IVA", "Total",
        "Método Pago", "Tracking", "Fecha Pedido", "Fecha Pago",
        "Fecha Envío", "Fecha Entrega", "Productos"
    ])
    
    # Data
    for order in orders:
        addr = order.shipping_address
        address_str = f"{addr.get('street', '')}, {addr.get('postal_code', '')} {addr.get('city', '')}"
        customer_name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}"
        items_str = ", ".join([f"{item.product_name} x{item.quantity}" for item in order.items])
        
        writer.writerow([
            order.order_number,
            order.status,
            order.customer_email,
            customer_name,
            address_str,
            float(order.subtotal),
            float(order.shipping_cost),
            float(order.discount),
            float(order.tax),
            float(order.total),
            order.payment_method,
            order.tracking_number,
            order.created_at.isoformat() if order.created_at else "",
            order.paid_at.isoformat() if order.paid_at else "",
            order.shipped_at.isoformat() if order.shipped_at else "",
            order.delivered_at.isoformat() if order.delivered_at else "",
            items_str,
        ])
    
    output.seek(0)
    
    filename = f"pedidos_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# Product Management
# =============================================================================

@router.get("/products", response_model=PaginatedResponse[ProductResponse])
def list_products_admin(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    include_inactive: bool = True,
):
    """List all products (admin)."""
    query = db.query(Product).options(
        joinedload(Product.variants).joinedload(ProductVariant.images),
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.nutrition),
    )

    if not include_inactive:
        query = query.filter(Product.is_active == True)

    if search:
        query = query.filter(
            Product.name.ilike(f"%{search}%") |
            Product.sku.ilike(f"%{search}%")
        )

    if category:
        query = query.join(Category).filter(Category.slug == category)

    query = query.order_by(Product.created_at.desc())

    total = query.count()
    offset = (page - 1) * page_size
    products = query.offset(offset).limit(page_size).all()

    from app.models.product import Review as ReviewModel
    from sqlalchemy import func
    product_ids = [p.id for p in products]
    review_stats = {}
    if product_ids:
        rows = db.query(
            ReviewModel.product_id,
            func.avg(ReviewModel.rating).label("avg"),
            func.count(ReviewModel.id).label("cnt"),
        ).filter(
            ReviewModel.product_id.in_(product_ids),
            ReviewModel.status == "approved",
        ).group_by(ReviewModel.product_id).all()
        review_stats = {r.product_id: (float(r.avg), int(r.cnt)) for r in rows}

    def _product_resp_with_stats(p: Product) -> ProductResponse:
        avg, cnt = review_stats.get(p.id, (None, 0))
        product_level_images = [img for img in p.images if img.variant_id is None]
        return ProductResponse(
            id=p.id, sku=p.sku, slug=p.slug, name=p.name,
            short_description=p.short_description, description=p.description,
            badge_color=p.badge_color, audio_url=p.audio_url, is_active=p.is_active, is_featured=p.is_featured,
            is_in_stock=p.is_in_stock, category=p.category, images=product_level_images,
            nutrition=p.nutrition, variants=[_variant_resp(v) for v in p.variants],
            average_rating=avg, review_count=cnt,
            created_at=p.created_at, updated_at=p.updated_at,
        )

    items = [_product_resp_with_stats(p) for p in products]
    return PaginatedResponse.create(items, total, page, page_size)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product_data: dict,
    db: DbSession,
    admin_user: AdminUser
):
    """Create a new product (admin). Variants are managed separately."""
    existing = db.query(Product).filter(Product.slug == product_data.get("slug")).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe un producto con este slug")

    allowed = {"sku", "slug", "name", "short_description", "description", "badge_color", "audio_url",
               "is_active", "is_featured", "category_id", "meta_title", "meta_description"}
    product = Product(**{k: v for k, v in product_data.items() if k in allowed})
    db.add(product)
    db.commit()
    db.refresh(product)

    product = db.query(Product).options(
        joinedload(Product.variants).joinedload(ProductVariant.images),
        joinedload(Product.images), joinedload(Product.category),
        joinedload(Product.nutrition), joinedload(Product.reviews),
    ).filter(Product.id == product.id).first()

    return _product_response(product)


@router.put("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    product_data: dict,
    db: DbSession,
    admin_user: AdminUser
):
    """Update a product (admin)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")

    if "slug" in product_data and product_data["slug"] != product.slug:
        if db.query(Product).filter(Product.slug == product_data["slug"], Product.id != product_id).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe un producto con este slug")

    allowed = {"sku", "slug", "name", "short_description", "description", "badge_color", "audio_url",
               "is_active", "is_featured", "category_id", "meta_title", "meta_description"}
    for field, value in product_data.items():
        if field in allowed:
            setattr(product, field, value)

    db.commit()
    db.refresh(product)

    product = db.query(Product).options(
        joinedload(Product.variants).joinedload(ProductVariant.images),
        joinedload(Product.images), joinedload(Product.category),
        joinedload(Product.nutrition), joinedload(Product.reviews),
    ).filter(Product.id == product.id).first()

    return _product_response(product)


@router.delete("/products/{product_id}", response_model=Message)
def delete_product(
    product_id: int,
    db: DbSession,
    admin_user: AdminUser
):
    """Delete a product (admin). Soft delete by setting is_active=False."""
    product = db.query(Product).filter(Product.id == product_id).first()

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )

    # Soft delete
    product.is_active = False
    db.commit()

    return Message(message="Producto eliminado")


# =============================================================================
# Image Upload
# =============================================================================

@router.post("/upload-image")
async def upload_image(
    admin_user: AdminUser,
    file: UploadFile = File(...),
    dest_path: str = Form(...),
):
    """
    Upload an image to Vercel Blob under images/{dest_path}/.
    Returns the public CDN URL.

    dest_path examples:
      "products/Crema Pistacho Pura/200gr"
      "blog"
      "categories"
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no permitido. Usa: {', '.join(_ALLOWED_EXTENSIONS)}",
        )

    clean_path = _safe_dest(dest_path)
    safe_name = re.sub(r"[^a-zA-Z0-9._\-]", "_", os.path.basename(file.filename or "file"))
    filename = f"{_uuid.uuid4().hex[:8]}_{safe_name}"
    pathname = f"images/{clean_path}/{filename}"

    content = await file.read()
    try:
        public_url = await blob_service.upload(content, pathname)
    except Exception as exc:
        logger.error("Image upload failed: pathname=%s error=%s", pathname, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error subiendo imagen: {exc}")

    logger.info("Image uploaded: pathname=%s size=%d url=%s", pathname, len(content), public_url)
    return {"url": public_url, "filename": filename}


_ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".ogg"}


@router.post("/upload-audio")
async def upload_audio(
    admin_user: AdminUser,
    file: UploadFile = File(...),
    dest_path: str = Form(...),
):
    """
    Upload an audio clip to Vercel Blob under audios/{dest_path}/.
    Returns the public CDN URL.

    dest_path examples:
      "products/Crema Pistacho Pura"
      "products/Crema Pistacho Crunchy"
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no permitido. Usa: {', '.join(_ALLOWED_AUDIO_EXTENSIONS)}",
        )

    clean_path = _safe_dest(dest_path)
    safe_name = re.sub(r"[^a-zA-Z0-9._\-]", "_", os.path.basename(file.filename or "file"))
    filename = f"{_uuid.uuid4().hex[:8]}_{safe_name}"
    pathname = f"audios/{clean_path}/{filename}"

    content = await file.read()
    try:
        public_url = await blob_service.upload(content, pathname)
    except Exception as exc:
        logger.error("Audio upload failed: pathname=%s error=%s", pathname, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error subiendo audio: {exc}")

    logger.info("Audio uploaded: pathname=%s size=%d url=%s", pathname, len(content), public_url)
    return {"url": public_url, "filename": filename}


@router.put("/products/{product_id}/variants/{variant_id}", response_model=ProductVariantResponse)
def update_variant(
    product_id: int,
    variant_id: int,
    variant_data: dict,
    db: DbSession,
    admin_user: AdminUser,
):
    """Update a product variant's price, stock or active status (admin)."""
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id,
        ProductVariant.product_id == product_id,
    ).first()
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variante no encontrada")

    allowed = {"price", "compare_price", "stock", "is_active", "sku"}
    for field, value in variant_data.items():
        if field in allowed:
            setattr(variant, field, value)

    if "image_url" in variant_data and variant_data["image_url"]:
        new_url: str = variant_data["image_url"]
        existing = db.query(ProductImage).filter(
            ProductImage.variant_id == variant.id,
            ProductImage.is_primary == True,
        ).first()
        if existing:
            existing.url = new_url
        else:
            db.add(ProductImage(
                product_id=variant.product_id,
                variant_id=variant.id,
                url=new_url,
                is_primary=True,
                sort_order=0,
            ))

    db.commit()
    db.refresh(variant)
    return _variant_resp(variant)


# =============================================================================
# Review Management
# =============================================================================

@router.get("/reviews/pending", response_model=PaginatedResponse[dict])
def list_pending_reviews(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List pending reviews for moderation."""
    query = db.query(Review).options(
        joinedload(Review.product),
        joinedload(Review.user)
    ).filter(Review.status == "pending").order_by(Review.created_at.desc())

    total = query.count()
    reviews = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        {
            "id": r.id,
            "product_name": r.product.name if r.product else "N/A",
            "user_name": r.user.full_name if r.user else "Anónimo",
            "rating": r.rating,
            "title": r.title,
            "comment": r.comment,
            "is_verified_purchase": r.is_verified_purchase,
            "created_at": r.created_at,
        }
        for r in reviews
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.put("/reviews/{review_id}/approve", response_model=Message)
def approve_review(review_id: int, db: DbSession, admin_user: AdminUser):
    """Approve a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review no encontrada")
    
    review.status = "approved"
    db.commit()
    return Message(message="Review aprobada")


@router.put("/reviews/{review_id}/reject", response_model=Message)
def reject_review(review_id: int, db: DbSession, admin_user: AdminUser):
    """Reject a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review no encontrada")
    
    review.status = "rejected"
    db.commit()
    return Message(message="Review rechazada")
