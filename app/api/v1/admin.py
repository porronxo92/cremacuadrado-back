"""
Admin API endpoints - Dashboard, Order Management, Product CRUD.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_

from app.api.deps import DbSession, AdminUser
from app.models.user import User
from app.models.product import Product, Category, Review
from app.models.order import Order, OrderItem
from app.schemas.order import OrderResponse, OrderStatusUpdate
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse
from app.schemas.admin import DashboardStats
from app.schemas.common import Message, PaginatedResponse
from app.services.email import EmailService
from app.config import settings

router = APIRouter()


# =============================================================================
# Dashboard
# =============================================================================

@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(db: DbSession, admin_user: AdminUser):
    """Get dashboard statistics."""
    today = datetime.utcnow().date()
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
async def list_all_orders(
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
async def get_order_admin(order_id: int, db: DbSession, admin_user: AdminUser):
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


@router.put("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: DbSession,
    admin_user: AdminUser
):
    """Update order status (admin)."""
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )
    
    old_status = order.status
    order.status = status_data.status
    
    # Update timestamps
    if status_data.status == "shipped":
        order.shipped_at = datetime.utcnow()
        if status_data.tracking_number:
            order.tracking_number = status_data.tracking_number
    elif status_data.status == "delivered":
        order.delivered_at = datetime.utcnow()
    
    if status_data.admin_notes:
        order.admin_notes = status_data.admin_notes
    
    db.commit()
    db.refresh(order)
    
    # Send notification email
    customer_email = order.customer_email
    customer_name = order.shipping_address.get("first_name", "Cliente")
    
    if status_data.status == "shipped" and order.tracking_number:
        EmailService.send_order_shipped_email(
            to_email=customer_email,
            order_number=order.order_number,
            customer_name=customer_name,
            tracking_number=order.tracking_number,
        )
    elif old_status != status_data.status:
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


@router.get("/orders/export/csv")
async def export_orders_csv(
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
    
    filename = f"pedidos_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# Product Management
# =============================================================================

@router.get("/products", response_model=PaginatedResponse[ProductResponse])
async def list_products_admin(
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
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.nutrition),
        joinedload(Product.reviews),
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
    
    items = [
        ProductResponse(
            id=p.id,
            sku=p.sku,
            slug=p.slug,
            name=p.name,
            short_description=p.short_description,
            description=p.description,
            price=p.price,
            compare_price=p.compare_price,
            stock=p.stock,
            weight=p.weight,
            is_active=p.is_active,
            is_featured=p.is_featured,
            is_in_stock=p.is_in_stock,
            is_low_stock=p.is_low_stock,
            category=p.category,
            images=p.images,
            nutrition=p.nutrition,
            average_rating=p.average_rating,
            review_count=p.review_count,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in products
    ]
    
    return PaginatedResponse.create(items, total, page, page_size)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    db: DbSession,
    admin_user: AdminUser
):
    """Create a new product (admin)."""
    # Check slug uniqueness
    existing = db.query(Product).filter(Product.slug == product_data.slug).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe un producto con este slug"
        )
    
    product = Product(**product_data.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    
    # Reload with relationships
    product = db.query(Product).options(
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.nutrition),
        joinedload(Product.reviews),
    ).filter(Product.id == product.id).first()
    
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


@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_data: ProductUpdate,
    db: DbSession,
    admin_user: AdminUser
):
    """Update a product (admin)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )
    
    # Check slug uniqueness if changing
    if product_data.slug and product_data.slug != product.slug:
        existing = db.query(Product).filter(
            Product.slug == product_data.slug,
            Product.id != product_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya existe un producto con este slug"
            )
    
    update_data = product_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)
    
    db.commit()
    db.refresh(product)
    
    # Reload with relationships
    product = db.query(Product).options(
        joinedload(Product.images),
        joinedload(Product.category),
        joinedload(Product.nutrition),
        joinedload(Product.reviews),
    ).filter(Product.id == product.id).first()
    
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


@router.delete("/products/{product_id}", response_model=Message)
async def delete_product(
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
# Review Management
# =============================================================================

@router.get("/reviews/pending", response_model=List[dict])
async def list_pending_reviews(db: DbSession, admin_user: AdminUser):
    """List pending reviews for moderation."""
    reviews = db.query(Review).options(
        joinedload(Review.product),
        joinedload(Review.user)
    ).filter(Review.status == "pending").order_by(Review.created_at.desc()).all()
    
    return [
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


@router.put("/reviews/{review_id}/approve", response_model=Message)
async def approve_review(review_id: int, db: DbSession, admin_user: AdminUser):
    """Approve a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review no encontrada")
    
    review.status = "approved"
    db.commit()
    return Message(message="Review aprobada")


@router.put("/reviews/{review_id}/reject", response_model=Message)
async def reject_review(review_id: int, db: DbSession, admin_user: AdminUser):
    """Reject a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review no encontrada")
    
    review.status = "rejected"
    db.commit()
    return Message(message="Review rechazada")
