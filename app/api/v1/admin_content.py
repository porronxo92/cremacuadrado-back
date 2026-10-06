"""
Admin API endpoints — Points of sale, coupons, and lead inboxes
(newsletter, B2B, contact form) for the CMS.
"""
import csv
import io
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import String, func
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, AdminUser
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.order import Coupon, CouponRedemption, Order
from app.models.point_of_sale import PointOfSale
from app.services.geocoding import geocode_point_of_sale
from app.models.pos_lead import PosLead
from app.schemas.common import Message, PaginatedResponse
from app.schemas.coupon import (
    CouponAdminResponse, CouponCreate, CouponRedemptionItem, CouponUpdate,
)
from app.schemas.point_of_sale import (
    PointOfSaleAdminResponse, PointOfSaleCreate, PointOfSaleUpdate,
)

router = APIRouter()


# =============================================================================
# Points of Sale
# =============================================================================

@router.get("/points-of-sale", response_model=List[PointOfSaleAdminResponse])
def list_points_of_sale_admin(db: DbSession, admin_user: AdminUser):
    """List all points of sale (admin — includes inactive)."""
    return db.query(PointOfSale).order_by(PointOfSale.sort_order, PointOfSale.city).all()


@router.post("/points-of-sale", response_model=PointOfSaleAdminResponse, status_code=status.HTTP_201_CREATED)
def create_point_of_sale(data: PointOfSaleCreate, db: DbSession, admin_user: AdminUser):
    """Create a new point of sale."""
    if db.query(PointOfSale).filter(PointOfSale.name == data.name, PointOfSale.city == data.city).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe ese punto de venta en esa ciudad")

    store = PointOfSale(**data.model_dump())
    if store.latitude is not None and store.longitude is not None:
        store.geo_precision = "manual"
    else:
        geocode_point_of_sale(store)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.put("/points-of-sale/{store_id}", response_model=PointOfSaleAdminResponse)
def update_point_of_sale(store_id: int, data: PointOfSaleUpdate, db: DbSession, admin_user: AdminUser):
    """Update a point of sale."""
    store = db.query(PointOfSale).filter(PointOfSale.id == store_id).first()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Punto de venta no encontrado")

    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(store, field, value)

    if "latitude" in changes or "longitude" in changes:
        store.geo_precision = "manual" if store.latitude is not None and store.longitude is not None else None
    elif {"address", "city", "name"} & changes.keys() and store.geo_precision != "manual":
        geocode_point_of_sale(store)

    db.commit()
    db.refresh(store)
    return store


@router.post("/points-of-sale/{store_id}/geocode", response_model=PointOfSaleAdminResponse)
def geocode_point_of_sale_endpoint(store_id: int, db: DbSession, admin_user: AdminUser):
    """Busca las coordenadas del punto de venta a partir de su dirección (OpenStreetMap)."""
    store = db.query(PointOfSale).filter(PointOfSale.id == store_id).first()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Punto de venta no encontrado")
    if not geocode_point_of_sale(store):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No se han encontrado coordenadas: revisa la dirección o ponlas a mano")
    db.commit()
    db.refresh(store)
    return store


@router.delete("/points-of-sale/{store_id}", response_model=Message)
def delete_point_of_sale(store_id: int, db: DbSession, admin_user: AdminUser):
    """Delete a point of sale."""
    store = db.query(PointOfSale).filter(PointOfSale.id == store_id).first()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Punto de venta no encontrado")

    db.delete(store)
    db.commit()
    return Message(message="Punto de venta eliminado")


# =============================================================================
# Coupons
# =============================================================================

def _coupon_stats(db, coupon_ids: list[int]) -> dict[int, dict]:
    """Aggregate non-reverted redemptions per coupon."""
    if not coupon_ids:
        return {}
    try:
        rows = (
            db.query(
                CouponRedemption.coupon_id,
                func.count(CouponRedemption.id),
                func.count(func.distinct(func.coalesce(
                    func.cast(CouponRedemption.user_id, String), func.lower(CouponRedemption.email)
                ))),
                func.coalesce(func.sum(CouponRedemption.discount_amount), 0),
                func.coalesce(func.sum(Order.total), 0),
            )
            .join(Order, Order.id == CouponRedemption.order_id)
            .filter(CouponRedemption.coupon_id.in_(coupon_ids), CouponRedemption.reverted_at.is_(None))
            .group_by(CouponRedemption.coupon_id)
            .all()
        )
    except Exception:  # table not migrated yet
        db.rollback()
        return {}
    return {
        cid: {"redemptions": n, "unique_customers": u, "total_discount": d, "revenue": r}
        for cid, n, u, d, r in rows
    }


@router.get("/coupons", response_model=PaginatedResponse[CouponAdminResponse])
def list_coupons(
    db: DbSession, admin_user: AdminUser,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
):
    """List discount coupons with usage analytics."""
    query = db.query(Coupon)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(Coupon.code.ilike(like) | Coupon.description.ilike(like))
    if is_active is not None:
        query = query.filter(Coupon.is_active == is_active)
    total = query.count()
    coupons = query.order_by(Coupon.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    stats = _coupon_stats(db, [c.id for c in coupons])
    items = [
        CouponAdminResponse.model_validate(c).model_copy(update=stats.get(c.id, {}))
        for c in coupons
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/coupons/{coupon_id}/redemptions", response_model=PaginatedResponse[CouponRedemptionItem])
def list_coupon_redemptions(
    coupon_id: int, db: DbSession, admin_user: AdminUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    include_reverted: bool = True,
):
    """Who used this coupon: customer, order, date and discount applied."""
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cupón no encontrado")

    query = (
        db.query(CouponRedemption)
        .options(joinedload(CouponRedemption.order), joinedload(CouponRedemption.user))
        .filter(
            (CouponRedemption.coupon_id == coupon.id)
            | (func.upper(CouponRedemption.coupon_code) == coupon.code.upper())
        )
    )
    if not include_reverted:
        query = query.filter(CouponRedemption.reverted_at.is_(None))
    if search:
        like = f"%{search.strip()}%"
        query = query.outerjoin(Order, Order.id == CouponRedemption.order_id).filter(
            CouponRedemption.email.ilike(like) | Order.order_number.ilike(like)
        )
    total = query.order_by(None).count()
    rows = query.order_by(CouponRedemption.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for r in rows:
        name = r.user.full_name if r.user else None
        if not name and r.order:
            addr = r.order.shipping_address or {}
            name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip() or None
        items.append(CouponRedemptionItem(
            id=r.id, order_id=r.order_id,
            order_number=r.order.order_number if r.order else None,
            order_status=r.order.status if r.order else None,
            order_total=r.order.total if r.order else None,
            user_id=r.user_id, email=r.email, customer_name=name,
            discount_amount=r.discount_amount, reverted_at=r.reverted_at, created_at=r.created_at,
        ))
    return PaginatedResponse.create(items, total, page, page_size)


@router.post("/coupons", response_model=CouponAdminResponse, status_code=status.HTTP_201_CREATED)
def create_coupon(data: CouponCreate, db: DbSession, admin_user: AdminUser):
    """Create a discount coupon."""
    if db.query(Coupon).filter(Coupon.code == data.code).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe un cupón con este código")

    coupon = Coupon(**data.model_dump())
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.put("/coupons/{coupon_id}", response_model=CouponAdminResponse)
def update_coupon(coupon_id: int, data: CouponUpdate, db: DbSession, admin_user: AdminUser):
    """Update a discount coupon."""
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cupón no encontrado")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(coupon, field, value)

    db.commit()
    db.refresh(coupon)
    return coupon


@router.delete("/coupons/{coupon_id}", response_model=Message)
def delete_coupon(coupon_id: int, db: DbSession, admin_user: AdminUser):
    """Delete a discount coupon."""
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cupón no encontrado")

    db.delete(coupon)
    db.commit()
    return Message(message="Cupón eliminado")


# =============================================================================
# Leads (read-only inboxes + CSV export)
# =============================================================================

@router.get("/leads/newsletter", response_model=PaginatedResponse[dict])
def list_newsletter_leads(
    db: DbSession, admin_user: AdminUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    converted: Optional[bool] = None,
):
    """List newsletter popup leads (converted = registered an account afterwards)."""
    query = db.query(NewsletterLead)
    if search:
        query = query.filter(NewsletterLead.email.ilike(f"%{search.strip()}%"))
    if converted is True:
        query = query.filter(NewsletterLead.converted_at.isnot(None))
    elif converted is False:
        query = query.filter(NewsletterLead.converted_at.is_(None))
    query = query.order_by(NewsletterLead.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "email": r.email, "source": r.source,
            "coupon_code": r.coupon_code, "converted_at": r.converted_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/leads/pos", response_model=PaginatedResponse[dict])
def list_pos_leads(
    db: DbSession, admin_user: AdminUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
):
    """List B2B (para-tiendas) leads."""
    query = db.query(PosLead)
    if status_filter:
        query = query.filter(PosLead.status == status_filter)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            PosLead.email.ilike(like) | PosLead.name.ilike(like)
            | PosLead.establishment_name.ilike(like) | PosLead.city.ilike(like)
        )
    query = query.order_by(PosLead.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "name": r.name, "establishment_name": r.establishment_name,
            "city": r.city, "establishment_type": r.establishment_type,
            "email": r.email, "phone": r.phone, "stage": r.stage,
            "status": r.status, "notes": r.notes, "created_at": r.created_at,
        }
        for r in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


_VALID_POS_LEAD_STATUSES = {"new", "contacted", "sample_sent", "closed_won", "closed_lost"}


@router.patch("/leads/pos/{lead_id}/status", response_model=Message)
def update_pos_lead_status(
    lead_id: int, db: DbSession, admin_user: AdminUser,
    lead_status: str = Query(..., alias="status"),
):
    """Update the CRM status of a B2B lead."""
    if lead_status not in _VALID_POS_LEAD_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado no válido. Usa: {', '.join(sorted(_VALID_POS_LEAD_STATUSES))}",
        )

    lead = db.query(PosLead).filter(PosLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead no encontrado")

    lead.status = lead_status
    db.commit()
    return Message(message="Estado actualizado")


@router.patch("/leads/pos/{lead_id}", response_model=Message)
def update_pos_lead(lead_id: int, data: dict, db: DbSession, admin_user: AdminUser):
    """Update status and/or internal notes of a B2B lead."""
    lead = db.query(PosLead).filter(PosLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead no encontrado")
    if "status" in data:
        if data["status"] not in _VALID_POS_LEAD_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido")
        lead.status = data["status"]
    if "notes" in data:
        lead.notes = (str(data["notes"] or "")).strip()[:5000] or None
    db.commit()
    return Message(message="Lead actualizado")


@router.get("/leads/contact", response_model=PaginatedResponse[dict])
def list_contact_leads(
    db: DbSession, admin_user: AdminUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
):
    """List /contacto form submissions."""
    query = db.query(ContactLead)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(ContactLead.email.ilike(like) | ContactLead.name.ilike(like) | ContactLead.message.ilike(like))
    query = query.order_by(ContactLead.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "name": r.name, "email": r.email, "message": r.message,
            "accepts_marketing": r.accepts_marketing, "source": r.source,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


_LEAD_EXPORTERS = {
    "newsletter": (NewsletterLead, ["id", "email", "source", "coupon_code", "converted_at", "created_at"]),
    "pos": (PosLead, ["id", "name", "establishment_name", "city", "establishment_type", "email", "phone", "stage", "status", "notes", "created_at"]),
    "contact": (ContactLead, ["id", "name", "email", "message", "accepts_marketing", "source", "created_at"]),
}


@router.get("/leads/export/csv")
def export_leads_csv(
    db: DbSession, admin_user: AdminUser,
    lead_type: str = Query(..., alias="type", pattern="^(newsletter|pos|contact)$"),
):
    """Export a lead inbox (newsletter, pos, contact) to CSV."""
    model, columns = _LEAD_EXPORTERS[lead_type]
    rows = db.query(model).order_by(model.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([getattr(row, col) for col in columns])
    output.seek(0)

    filename = f"leads_{lead_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
