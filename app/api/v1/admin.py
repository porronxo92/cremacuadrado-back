"""
Admin API endpoints - Dashboard, Order Management, Product CRUD.
"""
from datetime import datetime, timedelta, timezone
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
from sqlalchemy import func

from app.api.deps import DbSession, AdminUser
from app.models.user import User
from app.models.product import Product, Category, Review, ProductVariant, ProductImage
from app.models.order import Order, OrderItem
from app.models.payment import PaymentIntent as PaymentIntentModel, Refund
from app.models.shipment import Shipment, ShipmentEvent
from app.schemas.order import OrderStatusUpdate
from app.schemas.product import ProductResponse, ProductVariantResponse
from app.schemas.admin import AdminOrderNotes, AdminOrderResponse
from app.services.coupon_redemptions import record_redemption, revert_redemption
from app.schemas.common import Message, PaginatedResponse
from app.services.email import EmailService
from app.services import blob_service
from app.config import settings

router = APIRouter()

VALID_ORDER_STATUSES = {
    "pending_payment", "payment_failed", "paid",
    "processing", "shipped", "delivered", "cancelled", "refunded", "partially_refunded",
}
PAID_ORDER_STATUSES = ("paid", "processing", "shipped", "delivered", "partially_refunded")

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
# Order Management
# =============================================================================

ORDER_SORT_FIELDS = {"created_at", "total", "order_number", "status", "paid_at"}


def _order_resp(order: Order) -> AdminOrderResponse:
    """Build the admin order payload (always includes customer email/name)."""
    addr = order.shipping_address or {}
    name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
    if not name and order.user:
        name = order.user.full_name
    return AdminOrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        shipping_cost=order.shipping_cost,
        discount=order.discount,
        coupon_code=order.coupon_code,
        tax=order.tax,
        total=order.total,
        shipping_address=addr,
        billing_address=order.billing_address,
        payment_method=order.payment_method,
        tracking_number=order.tracking_number,
        customer_notes=order.customer_notes,
        customer_email=order.customer_email,
        items=order.items,
        item_count=order.item_count,
        created_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        user_id=order.user_id,
        guest_email=order.guest_email,
        customer_name=name or None,
        admin_notes=order.admin_notes,
        payment_intent_id=order.payment_intent_id,
        shipping_status=order.shipping_status,
        updated_at=order.updated_at,
    )


def _filtered_orders_query(
    db, status_filter=None, date_from=None, date_to=None, search=None,
    coupon_code=None, user_id=None,
):
    query = db.query(Order).options(joinedload(Order.items), joinedload(Order.user))
    if status_filter:
        statuses = [st.strip() for st in status_filter.split(",") if st.strip()]
        query = query.filter(Order.status.in_(statuses))
    if date_from:
        query = query.filter(Order.created_at >= date_from)
    if date_to:
        # A bare date (00:00:00) means "until the end of that day"
        if date_to.hour == 0 and date_to.minute == 0 and date_to.second == 0:
            query = query.filter(Order.created_at < date_to + timedelta(days=1))
        else:
            query = query.filter(Order.created_at <= date_to)
    if coupon_code:
        query = query.filter(func.upper(Order.coupon_code) == coupon_code.strip().upper())
    if user_id:
        query = query.filter(Order.user_id == user_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.outerjoin(User, Order.user_id == User.id).filter(
            Order.order_number.ilike(like)
            | Order.guest_email.ilike(like)
            | User.email.ilike(like)
            | Order.shipping_address_json.ilike(like)
            | Order.tracking_number.ilike(like)
        )
    return query


@router.get("/orders", response_model=PaginatedResponse[AdminOrderResponse])
def list_all_orders(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: Optional[int] = Query(None, ge=1, le=100),
    status: Optional[str] = Query(None, description="Uno o varios estados separados por coma"),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
    coupon_code: Optional[str] = None,
    user_id: Optional[int] = None,
    sort: str = "created_at",
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List orders with filters, sorting and pagination (admin only)."""
    # Accept 'limit' as an alias for 'page_size' (frontend compatibility)
    if limit is not None:
        page_size = limit

    query = _filtered_orders_query(db, status, date_from, date_to, search, coupon_code, user_id)
    total = query.order_by(None).count()

    col = getattr(Order, sort if sort in ORDER_SORT_FIELDS else "created_at")
    col = col.asc() if order == "asc" else col.desc()
    orders = (
        query.order_by(col.nulls_last(), Order.id.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return PaginatedResponse.create([_order_resp(o) for o in orders], total, page, page_size)


@router.get("/orders/{order_id}", response_model=AdminOrderResponse)
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

    return _order_resp(order)


@router.patch("/orders/{order_id}/notes", response_model=AdminOrderResponse)
def update_order_notes(order_id: int, data: AdminOrderNotes, db: DbSession, admin_user: AdminUser):
    """Save internal admin notes for an order (never shown to the customer)."""
    order = db.query(Order).options(
        joinedload(Order.items), joinedload(Order.user)
    ).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")
    order.admin_notes = (data.admin_notes or "").strip() or None
    db.commit()
    db.refresh(order)
    return _order_resp(order)


@router.get("/orders/{order_id}/payments")
def get_order_payments(order_id: int, db: DbSession, admin_user: AdminUser):
    """Stripe payment intents and refunds linked to an order."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    intents = (
        db.query(PaymentIntentModel)
        .filter(PaymentIntentModel.order_id == order_id)
        .order_by(PaymentIntentModel.created_at.desc())
        .all()
    )
    refunds = db.query(Refund).filter(Refund.order_id == order_id).order_by(Refund.created_at.desc()).all()
    return {
        "payment_intents": [
            {
                "id": pi.id,
                "stripe_payment_intent_id": pi.stripe_payment_intent_id,
                "amount": pi.amount / 100,
                "currency": pi.currency,
                "status": pi.status,
                "payment_method_type": pi.payment_method_type,
                "created_at": pi.created_at,
                "updated_at": pi.updated_at,
            }
            for pi in intents
        ],
        "refunds": [
            {
                "id": r.id,
                "stripe_refund_id": r.stripe_refund_id,
                "amount": r.amount / 100,
                "reason": r.reason,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in refunds
        ],
    }


def _do_update_order_status(order_id: int, status_data: OrderStatusUpdate, db, admin_user):
    """Shared logic for PUT and PATCH on order status."""
    order = db.query(Order).options(
        joinedload(Order.items),
        joinedload(Order.user),
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
    had_tracking = bool(order.tracking_number)
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

    if status_data.status in ("cancelled", "refunded"):
        revert_redemption(db, order)
    elif old_status in ("cancelled", "refunded") and status_data.status in PAID_ORDER_STATUSES:
        record_redemption(db, order)

    db.commit()
    db.refresh(order)

    customer_email = order.customer_email
    customer_name = order.shipping_address.get("first_name", "Cliente")

    if old_status != status_data.status:
        # Notify customer
        if status_data.status == "shipped" and order.tracking_number and had_tracking:
            # Shipped email already sent when the tracking number was first set
            pass
        elif status_data.status == "shipped" and order.tracking_number:
            sent = EmailService.send_order_shipped_email(
                to_email=customer_email,
                order_number=order.order_number,
                customer_name=customer_name,
                tracking_number=order.tracking_number,
            )
            if not sent:
                logger.error("Order shipped email failed: order=%s to=%s", order.order_number, customer_email)
            else:
                logger.info("Order shipped email sent: order=%s to=%s", order.order_number, customer_email)
        elif customer_email:
            sent = EmailService.send_order_status_update_email(
                to_email=customer_email,
                order_number=order.order_number,
                customer_name=customer_name,
                new_status=status_data.status,
            )
            if not sent:
                logger.error("Order status update email failed: order=%s status=%s to=%s", order.order_number, status_data.status, customer_email)
            else:
                logger.info("Order status update email sent: order=%s status=%s to=%s", order.order_number, status_data.status, customer_email)

    return _order_resp(order)


@router.put("/orders/{order_id}/status", response_model=AdminOrderResponse)
def update_order_status_put(order_id: int, status_data: OrderStatusUpdate, db: DbSession, admin_user: AdminUser):
    """Update order status (admin) — PUT."""
    return _do_update_order_status(order_id, status_data, db, admin_user)


@router.patch("/orders/{order_id}/status", response_model=AdminOrderResponse)
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
            "events": [
                {
                    "id": ev.id,
                    "code": ev.code,
                    "description": ev.description,
                    "status": ev.status,
                    "occurred_at": ev.occurred_at,
                }
                for ev in (shipment.events or [])
            ],
        }
    }


@router.patch("/orders/{order_id}/tracking", response_model=AdminOrderResponse)
def update_tracking_number(
    order_id: int,
    db: DbSession,
    admin_user: AdminUser,
    tracking_number: str = Query(..., description="Número de seguimiento Correos"),
):
    """Manually set or update the tracking number for an order.

    Sends the shipping notification email to the customer the first time a
    tracking number is set (CORREOS_ENABLED=False manual workflow).
    """
    order = db.query(Order).options(
        joinedload(Order.items), joinedload(Order.user)
    ).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    is_new_tracking = not order.tracking_number
    order.tracking_number = tracking_number

    # Also update the associated shipment localizador if one exists
    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if shipment:
        shipment.localizador = tracking_number

    db.commit()
    db.refresh(order)

    if is_new_tracking:
        customer_email = order.customer_email
        if customer_email:
            sent = EmailService.send_order_shipped_email(
                to_email=customer_email,
                order_number=order.order_number,
                customer_name=order.shipping_address.get("first_name", "Cliente"),
                tracking_number=tracking_number,
            )
            if not sent:
                logger.error("Shipping email failed: order=%s to=%s", order.order_number, customer_email)
            else:
                logger.info("Shipping email sent: order=%s to=%s", order.order_number, customer_email)

    return _order_resp(order)


# ── Correos shipping: Labels, Tracking, Pickups ──────────────────────


@router.get("/orders/{order_id}/label")
def get_order_label(order_id: int, db: DbSession, admin_user: AdminUser):
    """Download the shipping label PDF for an order."""
    from app.services.correos.labels import get_label_pdf

    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if not shipment or not shipment.localizador:
        raise HTTPException(status_code=404, detail="No hay envío prerregistrado para este pedido")

    try:
        pdf_bytes = get_label_pdf(shipment.localizador)
    except Exception as exc:
        logger.error("Label generation failed for order %d: %s", order_id, exc)
        raise HTTPException(status_code=502, detail=f"Error al generar etiqueta: {exc}")

    filename = f"etiqueta_{shipment.localizador}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/orders/{order_id}/tracking/sync")
def sync_order_tracking(order_id: int, db: DbSession, admin_user: AdminUser):
    """Force a tracking sync for an order's shipment (queries Correos trackpub)."""
    from app.services.correos.tracking import sync_tracking_for_shipment

    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if not shipment or not shipment.localizador:
        raise HTTPException(status_code=404, detail="No hay envío prerregistrado para este pedido")

    try:
        new_events = sync_tracking_for_shipment(db, shipment)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Tracking sync failed for order %d: %s", order_id, exc)
        raise HTTPException(status_code=502, detail=f"Error al consultar tracking: {exc}")

    return {
        "new_events": new_events,
        "status": shipment.status,
        "events": [
            {
                "id": ev.id,
                "code": ev.code,
                "description": ev.description,
                "status": ev.status,
                "occurred_at": ev.occurred_at,
            }
            for ev in shipment.events
        ],
    }


@router.post("/shipping/pickup")
def request_shipping_pickup(
    db: DbSession,
    admin_user: AdminUser,
    pickup_date: Optional[str] = Query(None, description="Fecha de recogida (YYYY-MM-DD). Por defecto mañana."),
    estimated_shipments: int = Query(1, description="Número estimado de paquetes"),
    observations: str = Query("", description="Observaciones para el repartidor"),
):
    """Request a package pickup from Correos at the configured sender address."""
    from app.services.correos.pickups import request_pickup as _request_pickup
    from datetime import date

    parsed_date = None
    if pickup_date:
        try:
            parsed_date = date.fromisoformat(pickup_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de fecha inválido. Usa YYYY-MM-DD")

    try:
        result = _request_pickup(
            pickup_date=parsed_date,
            estimated_shipments=estimated_shipments,
            observations=observations,
        )
    except Exception as exc:
        logger.error("Pickup request failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Error al solicitar recogida: {exc}")

    return {"pickup": result}


@router.post("/orders/{order_id}/cancel-shipment")
def cancel_order_shipment(order_id: int, db: DbSession, admin_user: AdminUser):
    """Cancel the preregistered shipment for an order (before Correos picks it up)."""
    from app.services.correos.preregister import cancel_shipment as _cancel

    shipment = db.query(Shipment).filter(Shipment.order_id == order_id).first()
    if not shipment or not shipment.localizador:
        raise HTTPException(status_code=404, detail="No hay envío prerregistrado para este pedido")

    if shipment.status in ("delivered", "returned", "cancelled"):
        raise HTTPException(status_code=409, detail=f"El envío ya está en estado '{shipment.status}'")

    try:
        result = _cancel(shipment.localizador)
    except Exception as exc:
        logger.error("Shipment cancel failed for order %d: %s", order_id, exc)
        raise HTTPException(status_code=502, detail=f"Error al anular envío: {exc}")

    shipment.status = "cancelled"
    shipment.updated_at = datetime.now(timezone.utc)
    order = db.query(Order).filter(Order.id == order_id).first()
    if order:
        order.shipping_status = "cancelled"
    db.commit()

    return {"message": "Envío anulado", "correos_response": result}


@router.get("/orders/export/csv")
def export_orders_csv(
    db: DbSession,
    admin_user: AdminUser,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    coupon_code: Optional[str] = None,
    user_id: Optional[int] = None,
):
    """Export orders to CSV (same filters as the list)."""
    query = _filtered_orders_query(db, status, date_from, date_to, search, coupon_code, user_id)
    orders = query.order_by(Order.created_at.desc()).all()

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        "Nº Pedido", "Estado", "Email", "Cliente", "Dirección",
        "Subtotal", "Envío", "Descuento", "Cupón", "IVA", "Total",
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
            order.coupon_code or "",
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
        iter(["\ufeff" + output.getvalue()]),  # BOM: Excel opens accents correctly
        media_type="text/csv; charset=utf-8",
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

_REVIEW_STATUSES = {"pending", "approved", "rejected"}


@router.get("/reviews", response_model=PaginatedResponse[dict])
def list_reviews(
    db: DbSession,
    admin_user: AdminUser,
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    product_id: Optional[int] = None,
    rating: Optional[int] = Query(None, ge=1, le=5),
    search: Optional[str] = None,
):
    """List reviews filtered by moderation status (pending/approved/rejected/all)."""
    if status != "all" and status not in _REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"status debe ser uno de: all, {', '.join(_REVIEW_STATUSES)}")

    query = db.query(Review).options(joinedload(Review.product), joinedload(Review.user))
    if status != "all":
        query = query.filter(Review.status == status)
    if product_id:
        query = query.filter(Review.product_id == product_id)
    if rating:
        query = query.filter(Review.rating == rating)
    if search:
        like = f"%{search.strip()}%"
        query = query.outerjoin(User, Review.user_id == User.id).filter(
            Review.title.ilike(like) | Review.comment.ilike(like) | User.email.ilike(like)
        )
    query = query.order_by(Review.created_at.desc())

    total = query.order_by(None).count()
    reviews = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        {
            "id": r.id,
            "product_id": r.product_id,
            "product_name": r.product.name if r.product else "N/A",
            "user_id": r.user_id,
            "user_name": r.user.full_name if r.user else "Anónimo",
            "user_email": r.user.email if r.user else None,
            "rating": r.rating,
            "title": r.title,
            "comment": r.comment,
            "is_verified_purchase": r.is_verified_purchase,
            "status": r.status,
            "admin_response": r.admin_response,
            "created_at": r.created_at,
        }
        for r in reviews
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.put("/reviews/{review_id}/response", response_model=Message)
def respond_review(review_id: int, data: dict, db: DbSession, admin_user: AdminUser):
    """Save (or clear) the shop's public response to a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review no encontrada")
    text = (data.get("admin_response") or "").strip()
    review.admin_response = text[:2000] or None
    db.commit()
    return Message(message="Respuesta guardada")


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

