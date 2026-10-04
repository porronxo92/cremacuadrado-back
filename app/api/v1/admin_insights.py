"""
Admin API — analytics & customer-activity visibility:
dashboard, abandoned carts, payments, Stripe webhook log, shipments, low stock.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func
from sqlalchemy.orm import joinedload

from app.api.deps import AdminUser, DbSession
from app.models.cart import Cart, CartItem
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.order import CouponRedemption, Order, OrderItem
from app.models.payment import PaymentIntent, Refund, StripeWebhookEvent
from app.models.pos_lead import PosLead
from app.models.product import Product, ProductVariant, Review
from app.models.shipment import Shipment
from app.models.user import User
from app.schemas.admin import DashboardStats
from app.schemas.common import PaginatedResponse

router = APIRouter()

PAID_STATUSES = ("paid", "processing", "shipped", "delivered", "partially_refunded")
ABANDONED_AFTER_HOURS = 24
ABANDONED_LOOKBACK_DAYS = 30


def _utcnow_naive() -> datetime:
    # DB timestamps are naive UTC
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _abandoned_carts_query(db, min_age_hours: int, max_age_days: Optional[int]):
    now = _utcnow_naive()
    q = (
        db.query(Cart)
        .join(CartItem, CartItem.cart_id == Cart.id)
        .filter(Cart.updated_at <= now - timedelta(hours=min_age_hours))
        .group_by(Cart.id)
    )
    if max_age_days:
        q = q.filter(Cart.updated_at >= now - timedelta(days=max_age_days))
    return q


# =============================================================================
# Dashboard
# =============================================================================

@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard(
    db: DbSession,
    admin_user: AdminUser,
    period: int = Query(30, ge=1, le=366, description="Días hacia atrás"),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    """Dashboard statistics for a period (default: last 30 days) vs. the previous period."""
    now = _utcnow_naive()
    today_start = datetime.combine(now.date(), datetime.min.time())

    if date_from:
        period_start = date_from.replace(tzinfo=None)
        period_end = (date_to.replace(tzinfo=None) + timedelta(days=1)) if date_to else today_start + timedelta(days=1)
    else:
        period_end = today_start + timedelta(days=1)
        period_start = period_end - timedelta(days=period)
    span = period_end - period_start
    period_days = max(1, span.days)
    prev_start = period_start - span

    def in_period(col, start=period_start, end=period_end):
        return and_(col >= start, col < end)

    paid = Order.status.in_(PAID_STATUSES)

    # All-time totals
    total_orders = db.query(func.count(Order.id)).filter(paid).scalar() or 0
    pending_orders = db.query(func.count(Order.id)).filter(Order.status.in_(["paid", "processing"])).scalar() or 0
    total_revenue = db.query(func.sum(Order.total)).filter(paid).scalar() or Decimal("0")
    total_customers = db.query(func.count(User.id)).filter(User.role == "customer").scalar() or 0

    # Today
    orders_today = db.query(func.count(Order.id)).filter(Order.created_at >= today_start, paid).scalar() or 0
    revenue_today = db.query(func.sum(Order.total)).filter(Order.created_at >= today_start, paid).scalar() or Decimal("0")

    # Period
    orders_period = db.query(func.count(Order.id)).filter(
        in_period(Order.created_at), Order.status != "cancelled"
    ).scalar() or 0
    paid_orders_period = db.query(func.count(Order.id)).filter(in_period(Order.created_at), paid).scalar() or 0
    revenue_period = db.query(func.sum(Order.total)).filter(in_period(Order.created_at), paid).scalar() or Decimal("0")

    paid_prev = db.query(func.count(Order.id)).filter(in_period(Order.created_at, prev_start, period_start), paid).scalar() or 0
    revenue_prev = db.query(func.sum(Order.total)).filter(
        in_period(Order.created_at, prev_start, period_start), paid
    ).scalar() or Decimal("0")

    orders_growth = ((paid_orders_period - paid_prev) / paid_prev * 100) if paid_prev else None
    revenue_growth = float((revenue_period - revenue_prev) / revenue_prev * 100) if revenue_prev else None
    # Average ticket uses PAID orders only (numerator and denominator consistent)
    avg_order_value = (revenue_period / paid_orders_period) if paid_orders_period else Decimal("0")

    top_products = [
        {"product_name": name, "quantity_sold": int(qty or 0), "revenue": float(rev or 0)}
        for name, qty, rev in (
            db.query(OrderItem.product_name, func.sum(OrderItem.quantity), func.sum(OrderItem.total))
            .join(Order, Order.id == OrderItem.order_id)
            .filter(in_period(Order.created_at), paid)
            .group_by(OrderItem.product_name)
            .order_by(func.sum(OrderItem.total).desc())
            .limit(5).all()
        )
    ]

    orders_by_status = {
        st: cnt for st, cnt in db.query(Order.status, func.count(Order.id))
        .filter(in_period(Order.created_at)).group_by(Order.status).all()
    }

    # Daily series (filled with zeros)
    day = func.date(Order.created_at)
    rows = (
        db.query(day, func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .filter(in_period(Order.created_at), paid)
        .group_by(day).all()
    )
    by_day = {str(d): (int(c), float(r)) for d, c, r in rows}
    daily_series = []
    cursor = period_start.date()
    while cursor < period_end.date():
        c, r = by_day.get(str(cursor), (0, 0.0))
        daily_series.append({"date": str(cursor), "orders": c, "revenue": r})
        cursor += timedelta(days=1)

    new_customers_period = db.query(func.count(User.id)).filter(
        User.role == "customer", in_period(User.created_at)
    ).scalar() or 0

    # Returning-customer rate: buyers in period that have >1 paid order overall
    buyer_ids = [
        uid for (uid,) in db.query(Order.user_id).filter(
            in_period(Order.created_at), paid, Order.user_id.isnot(None)
        ).distinct().all()
    ]
    returning_customer_rate = None
    if buyer_ids:
        repeaters = (
            db.query(Order.user_id).filter(Order.user_id.in_(buyer_ids), paid)
            .group_by(Order.user_id).having(func.count(Order.id) > 1).count()
        )
        returning_customer_rate = round(repeaters / len(buyer_ids) * 100, 1)

    try:
        top_coupons = [
            {"code": code, "uses": int(uses), "discount": float(disc or 0)}
            for code, uses, disc in (
                db.query(CouponRedemption.coupon_code, func.count(CouponRedemption.id),
                         func.sum(CouponRedemption.discount_amount))
                .filter(in_period(CouponRedemption.created_at), CouponRedemption.reverted_at.is_(None))
                .group_by(CouponRedemption.coupon_code)
                .order_by(func.count(CouponRedemption.id).desc())
                .limit(5).all()
            )
        ]
    except Exception:  # table not migrated yet
        db.rollback()
        top_coupons = []

    abandoned_q = _abandoned_carts_query(db, ABANDONED_AFTER_HOURS, ABANDONED_LOOKBACK_DAYS)
    abandoned_ids = [c.id for c in abandoned_q.with_entities(Cart.id).all()]
    abandoned_value = Decimal("0")
    if abandoned_ids:
        abandoned_value = db.query(
            func.coalesce(func.sum(CartItem.price_at_add * CartItem.quantity), 0)
        ).filter(CartItem.cart_id.in_(abandoned_ids)).scalar() or Decimal("0")

    low_stock_variants = db.query(func.count(ProductVariant.id)).filter(
        ProductVariant.is_active == True,  # noqa: E712
        ProductVariant.stock <= ProductVariant.low_stock_threshold,
    ).scalar() or 0
    pending_reviews = db.query(func.count(Review.id)).filter(Review.status == "pending").scalar() or 0

    new_leads_period = {
        "newsletter": db.query(func.count(NewsletterLead.id)).filter(in_period(NewsletterLead.created_at)).scalar() or 0,
        "pos": db.query(func.count(PosLead.id)).filter(in_period(PosLead.created_at)).scalar() or 0,
        "contact": db.query(func.count(ContactLead.id)).filter(in_period(ContactLead.created_at)).scalar() or 0,
    }

    return DashboardStats(
        total_orders=total_orders,
        pending_orders=pending_orders,
        total_revenue=total_revenue,
        total_customers=total_customers,
        orders_today=orders_today,
        revenue_today=revenue_today,
        orders_period=orders_period,
        revenue_period=revenue_period,
        average_order_value=avg_order_value,
        top_products=top_products,
        orders_by_status=orders_by_status,
        orders_growth=orders_growth,
        revenue_growth=revenue_growth,
        period_days=period_days,
        period_start=period_start,
        period_end=period_end,
        paid_orders_period=paid_orders_period,
        new_customers_period=new_customers_period,
        returning_customer_rate=returning_customer_rate,
        daily_series=daily_series,
        top_coupons=top_coupons,
        abandoned_carts=len(abandoned_ids),
        abandoned_carts_value=abandoned_value,
        low_stock_variants=low_stock_variants,
        pending_reviews=pending_reviews,
        new_leads_period=new_leads_period,
    )


# =============================================================================
# Abandoned carts
# =============================================================================

@router.get("/carts", response_model=PaginatedResponse[dict])
def list_carts(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    min_age_hours: int = Query(ABANDONED_AFTER_HOURS, ge=0, le=24 * 365),
    max_age_days: Optional[int] = Query(None, ge=1, le=3650),
    only_registered: bool = False,
    search: Optional[str] = None,
):
    """Non-empty carts not touched for `min_age_hours` (0 = every active cart)."""
    q = _abandoned_carts_query(db, min_age_hours, max_age_days)
    if only_registered:
        q = q.filter(Cart.user_id.isnot(None))
    if search:
        like = f"%{search.strip()}%"
        q = q.join(User, User.id == Cart.user_id).filter(
            User.email.ilike(like) | User.first_name.ilike(like) | User.last_name.ilike(like)
        )
    total = q.order_by(None).count()
    ids = [
        cid for (cid,) in q.with_entities(Cart.id)
        .order_by(Cart.updated_at.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    ]
    carts = (
        db.query(Cart)
        .options(
            joinedload(Cart.user),
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
        )
        .filter(Cart.id.in_(ids)).all()
    ) if ids else []
    carts.sort(key=lambda c: c.updated_at, reverse=True)

    items = [
        {
            "id": c.id,
            "user_id": c.user_id,
            "email": c.user.email if c.user else None,
            "customer_name": c.user.full_name if c.user else None,
            "is_guest": c.user_id is None,
            "coupon_code": c.coupon_code,
            "item_count": c.item_count,
            "subtotal": float(c.subtotal),
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "items": [
                {
                    "product_name": it.product.name if it.product else "—",
                    "format": it.variant.format if it.variant else None,
                    "quantity": it.quantity,
                    "unit_price": float(it.price_at_add),
                    "total": float(it.total),
                }
                for it in c.items
            ],
        }
        for c in carts
    ]
    return PaginatedResponse.create(items, total, page, page_size)


# =============================================================================
# Payments, refunds & Stripe webhook log
# =============================================================================

@router.get("/payments", response_model=PaginatedResponse[dict])
def list_payments(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """Stripe PaymentIntents with their order."""
    q = db.query(PaymentIntent).options(joinedload(PaymentIntent.order).joinedload(Order.user))
    if status:
        q = q.filter(PaymentIntent.status == status)
    if search:
        like = f"%{search.strip()}%"
        q = q.join(Order, Order.id == PaymentIntent.order_id).filter(
            Order.order_number.ilike(like) | PaymentIntent.stripe_payment_intent_id.ilike(like)
            | Order.guest_email.ilike(like)
        )
    total = q.order_by(None).count()
    rows = q.order_by(PaymentIntent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": pi.id,
            "order_id": pi.order_id,
            "order_number": pi.order.order_number if pi.order else None,
            "order_status": pi.order.status if pi.order else None,
            "email": pi.order.customer_email if pi.order else None,
            "stripe_payment_intent_id": pi.stripe_payment_intent_id,
            "amount": pi.amount / 100,
            "currency": pi.currency,
            "status": pi.status,
            "payment_method_type": pi.payment_method_type,
            "created_at": pi.created_at,
            "updated_at": pi.updated_at,
        }
        for pi in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/refunds", response_model=PaginatedResponse[dict])
def list_refunds(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = db.query(Refund, Order.order_number).outerjoin(Order, Order.id == Refund.order_id)
    total = q.order_by(None).count()
    rows = q.order_by(Refund.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "order_id": r.order_id, "order_number": num,
            "stripe_refund_id": r.stripe_refund_id, "amount": r.amount / 100,
            "reason": r.reason, "status": r.status, "created_at": r.created_at,
        }
        for r, num in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/webhook-events", response_model=PaginatedResponse[dict])
def list_webhook_events(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    only_errors: bool = False,
    event_type: Optional[str] = None,
):
    """Stripe webhook log (without payload) — useful to diagnose payment issues."""
    q = db.query(StripeWebhookEvent)
    if only_errors:
        q = q.filter(StripeWebhookEvent.error.isnot(None))
    if event_type:
        q = q.filter(StripeWebhookEvent.event_type == event_type)
    total = q.order_by(None).count()
    rows = q.order_by(StripeWebhookEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": e.id, "stripe_event_id": e.stripe_event_id, "event_type": e.event_type,
            "processed": e.processed, "processed_at": e.processed_at,
            "error": e.error, "created_at": e.created_at,
        }
        for e in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/webhook-events/{event_id}")
def get_webhook_event(event_id: int, db: DbSession, admin_user: AdminUser):
    e = db.query(StripeWebhookEvent).filter(StripeWebhookEvent.id == event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return {
        "id": e.id, "stripe_event_id": e.stripe_event_id, "event_type": e.event_type,
        "processed": e.processed, "processed_at": e.processed_at, "error": e.error,
        "created_at": e.created_at, "payload": e.payload,
    }


# =============================================================================
# Shipments
# =============================================================================

@router.get("/shipments", response_model=PaginatedResponse[dict])
def list_shipments(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    only_errors: bool = False,
    search: Optional[str] = None,
):
    q = db.query(Shipment).options(joinedload(Shipment.order).joinedload(Order.user), joinedload(Shipment.events))
    if status:
        q = q.filter(Shipment.status == status)
    if only_errors:
        q = q.filter(Shipment.error.isnot(None))
    if search:
        like = f"%{search.strip()}%"
        q = q.join(Order, Order.id == Shipment.order_id).filter(
            Order.order_number.ilike(like) | Shipment.localizador.ilike(like)
        )
    total = q.order_by(None).count()
    rows = q.order_by(Shipment.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for sh in rows:
        last = sh.events[-1] if sh.events else None
        items.append({
            "id": sh.id,
            "order_id": sh.order_id,
            "order_number": sh.order.order_number if sh.order else None,
            "email": sh.order.customer_email if sh.order else None,
            "localizador": sh.localizador,
            "status": sh.status,
            "error": sh.error,
            "last_event": (
                {"description": last.description, "status": last.status, "occurred_at": last.occurred_at}
                if last else None
            ),
            "created_at": sh.created_at,
            "updated_at": sh.updated_at,
        })
    return PaginatedResponse.create(items, total, page, page_size)


# =============================================================================
# Stock
# =============================================================================

@router.get("/stock/low")
def list_low_stock(db: DbSession, admin_user: AdminUser):
    """Active variants at or below their low-stock threshold (out of stock first)."""
    rows = (
        db.query(ProductVariant, Product.name, Product.id)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(
            ProductVariant.is_active == True,  # noqa: E712
            ProductVariant.stock <= ProductVariant.low_stock_threshold,
        )
        .order_by(ProductVariant.stock.asc(), Product.name)
        .all()
    )
    return [
        {
            "variant_id": v.id, "product_id": pid, "product_name": name,
            "format": v.format, "sku": v.sku, "stock": v.stock,
            "low_stock_threshold": v.low_stock_threshold,
        }
        for v, name, pid in rows
    ]
