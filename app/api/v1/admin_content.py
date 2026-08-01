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

from app.api.deps import DbSession, AdminUser
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.order import Coupon
from app.models.point_of_sale import PointOfSale
from app.models.pos_lead import PosLead
from app.schemas.common import Message, PaginatedResponse
from app.schemas.coupon import CouponCreate, CouponResponse, CouponUpdate
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

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(store, field, value)

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

@router.get("/coupons", response_model=List[CouponResponse])
def list_coupons(db: DbSession, admin_user: AdminUser, search: Optional[str] = None):
    """List discount coupons."""
    query = db.query(Coupon)
    if search:
        query = query.filter(Coupon.code.ilike(f"%{search}%"))
    return query.order_by(Coupon.created_at.desc()).all()


@router.post("/coupons", response_model=CouponResponse, status_code=status.HTTP_201_CREATED)
def create_coupon(data: CouponCreate, db: DbSession, admin_user: AdminUser):
    """Create a discount coupon."""
    if db.query(Coupon).filter(Coupon.code == data.code).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe un cupón con este código")

    coupon = Coupon(**data.model_dump())
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.put("/coupons/{coupon_id}", response_model=CouponResponse)
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
):
    """List newsletter popup leads."""
    query = db.query(NewsletterLead).order_by(NewsletterLead.created_at.desc())
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
):
    """List B2B (para-tiendas) leads."""
    query = db.query(PosLead)
    if status_filter:
        query = query.filter(PosLead.status == status_filter)
    query = query.order_by(PosLead.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "name": r.name, "establishment_name": r.establishment_name,
            "city": r.city, "establishment_type": r.establishment_type,
            "email": r.email, "phone": r.phone, "stage": r.stage,
            "status": r.status, "created_at": r.created_at,
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


@router.get("/leads/contact", response_model=PaginatedResponse[dict])
def list_contact_leads(
    db: DbSession, admin_user: AdminUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    """List /contacto form submissions."""
    query = db.query(ContactLead).order_by(ContactLead.created_at.desc())
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
    "pos": (PosLead, ["id", "name", "establishment_name", "city", "establishment_type", "email", "phone", "stage", "status", "created_at"]),
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
