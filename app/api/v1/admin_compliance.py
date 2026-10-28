"""
Admin API — cumplimiento: solicitudes de desistimiento, registro de accesos
del personal (para detectar brechas) y consulta de consentimientos.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import joinedload

from app.api.deps import AdminUser, DbSession
from app.models.compliance import AdminAuditLog, ConsentRecord, WithdrawalRequest
from app.schemas.common import PaginatedResponse

router = APIRouter()


# ── Desistimientos ──────────────────────────────────────────────────────────

class WithdrawalUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(received|accepted|refunded|rejected)$")
    admin_notes: Optional[str] = Field(None, max_length=5000)


def _withdrawal_dict(w: WithdrawalRequest) -> dict:
    return {
        "id": w.id,
        "reference": f"D-{w.id:06d}",
        "order_id": w.order_id,
        "order_number": w.order.order_number if w.order else None,
        "order_status": w.order.status if w.order else None,
        "email": w.email,
        "full_name": w.full_name,
        "items_text": w.items_text,
        "reason": w.reason,
        "within_term": w.within_term,
        "status": w.status,
        "admin_notes": w.admin_notes,
        "ack_sent_at": w.ack_sent_at,
        "requested_at": w.requested_at,
        "updated_at": w.updated_at,
    }


@router.get("/withdrawals", response_model=PaginatedResponse[dict])
def list_withdrawals(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
):
    q = db.query(WithdrawalRequest).options(joinedload(WithdrawalRequest.order))
    if status:
        q = q.filter(WithdrawalRequest.status == status)
    total = q.order_by(None).count()
    rows = q.order_by(WithdrawalRequest.requested_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedResponse.create([_withdrawal_dict(w) for w in rows], total, page, page_size)


@router.patch("/withdrawals/{withdrawal_id}")
def update_withdrawal(withdrawal_id: int, data: WithdrawalUpdate, db: DbSession, admin_user: AdminUser):
    w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if data.status is not None:
        w.status = data.status
    if data.admin_notes is not None:
        w.admin_notes = data.admin_notes
    db.commit()
    db.refresh(w)
    return _withdrawal_dict(w)


# ── Registro de accesos ─────────────────────────────────────────────────────

@router.get("/audit-log", response_model=PaginatedResponse[dict])
def list_audit_log(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin_email: Optional[str] = None,
    path: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    q = db.query(AdminAuditLog)
    if admin_email:
        q = q.filter(AdminAuditLog.admin_email.ilike(f"%{admin_email.strip()}%"))
    if path:
        q = q.filter(AdminAuditLog.path.ilike(f"%{path.strip()}%"))
    if date_from:
        q = q.filter(AdminAuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AdminAuditLog.created_at <= date_to)
    total = q.order_by(None).count()
    rows = q.order_by(AdminAuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": r.id, "admin_email": r.admin_email, "action": r.action, "path": r.path,
            "query": r.query, "status_code": r.status_code, "ip": r.ip, "created_at": r.created_at,
        }
        for r in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)


# ── Consentimientos ─────────────────────────────────────────────────────────

@router.get("/consents", response_model=PaginatedResponse[dict])
def list_consents(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    email: Optional[str] = None,
    purpose: Optional[str] = None,
):
    q = db.query(ConsentRecord)
    if email:
        q = q.filter(ConsentRecord.subject_email.ilike(f"%{email.strip().lower()}%"))
    if purpose:
        q = q.filter(ConsentRecord.purpose == purpose)
    total = q.order_by(None).count()
    rows = q.order_by(ConsentRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": c.id, "email": c.subject_email, "purpose": c.purpose, "granted": c.granted,
            "policy_version": c.policy_version, "source": c.source, "ip": c.ip, "created_at": c.created_at,
        }
        for c in rows
    ]
    return PaginatedResponse.create(items, total, page, page_size)
