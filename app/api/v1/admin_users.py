"""
Admin API — customer & administrator account management.

Safeguards (apply to every mutating endpoint):
  * an admin cannot demote, deactivate or delete their own account
  * the last active admin can never be demoted, deactivated or deleted
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from app.api.deps import AdminUser, DbSession
from app.models.cart import Cart, CartItem
from app.models.lead import NewsletterLead
from app.models.order import CouponRedemption, Order
from app.models.product import Review
from app.models.user import PasswordResetToken, User
from app.schemas.admin import (
    AdminSetPassword, AdminUserDetail, AdminUserItem, AdminUserStatusUpdate, AdminUserUpdate,
)
from app.schemas.common import Message, PaginatedResponse
from app.services.email import EmailService
from app.services.user_accounts import anonymize_user, revoke_sessions
from app.utils.security import generate_reset_token, get_password_hash

logger = logging.getLogger("cremacuadrado.admin.users")

router = APIRouter()

PAID_STATUSES = ("paid", "processing", "shipped", "delivered", "partially_refunded")

VALID_USER_SORT_FIELDS = {
    "created_at", "email", "first_name", "last_name", "total_orders", "total_spent", "last_login_at",
}


# =============================================================================
# Helpers
# =============================================================================

def _get_user_or_404(db, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


def _other_active_admins(db, user_id: int) -> int:
    return db.query(func.count(User.id)).filter(
        User.role == "admin", User.is_active == True, User.id != user_id  # noqa: E712
    ).scalar() or 0


def _guard_admin_loss(db, admin_user: User, target: User, *, demote=False, deactivate=False, delete=False) -> None:
    """Reject changes that would lock the shop out of its own admin panel."""
    if not (demote or deactivate or delete):
        return
    if target.id == admin_user.id:
        action = "eliminar" if delete else ("desactivar" if deactivate else "quitar el rol de administrador a")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No puedes {action} tu propia cuenta",
        )
    if target.role == "admin" and target.is_active and _other_active_admins(db, target.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe quedar al menos un administrador activo",
        )


def _reject_self(admin_user: User, target: User, detail: str) -> None:
    if target.id == admin_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _user_item(user: User, total_orders: int, total_spent, order_ids) -> AdminUserItem:
    return AdminUserItem(
        id=user.id, email=user.email, first_name=user.first_name, last_name=user.last_name,
        phone=user.phone, role=user.role, is_active=user.is_active,
        email_verified=user.email_verified, marketing_opt_in=user.marketing_opt_in,
        created_at=user.created_at, last_login_at=user.last_login_at,
        login_count=user.login_count or 0,
        total_orders=total_orders, total_spent=total_spent, order_ids=order_ids,
    )


# =============================================================================
# List
# =============================================================================

@router.get("/users", response_model=PaginatedResponse[AdminUserItem])
def list_all_users(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: Optional[int] = Query(None, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    has_orders: Optional[bool] = None,
    sort: Optional[str] = None,
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List users with filters, sorting, pagination and order stats."""
    if limit is not None:
        page_size = limit

    orders_count = func.count(Order.id).label("total_orders")
    orders_spent = func.coalesce(
        func.sum(case((Order.status.in_(PAID_STATUSES), Order.total), else_=0)), 0,
    ).label("total_spent")

    query = (
        db.query(User, orders_count, orders_spent)
        .outerjoin(Order, Order.user_id == User.id)
        .group_by(User.id)
    )

    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            User.email.ilike(like) | User.first_name.ilike(like)
            | User.last_name.ilike(like) | User.phone.ilike(like)
        )
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if has_orders is True:
        query = query.having(func.count(Order.id) > 0)
    elif has_orders is False:
        query = query.having(func.count(Order.id) == 0)

    total = query.order_by(None).count()

    sort_field = sort if sort in VALID_USER_SORT_FIELDS else "created_at"
    if sort_field == "total_orders":
        order_col = orders_count
    elif sort_field == "total_spent":
        order_col = orders_spent
    else:
        order_col = getattr(User, sort_field)
    order_col = order_col.asc() if order == "asc" else order_col.desc()
    if sort_field == "last_login_at":
        order_col = order_col.nulls_last()
    rows = query.order_by(order_col, User.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    user_ids = [u.id for u, _, _ in rows]
    order_ids_by_user: dict[int, list[int]] = {uid: [] for uid in user_ids}
    if user_ids:
        for uid, oid in (
            db.query(Order.user_id, Order.id)
            .filter(Order.user_id.in_(user_ids))
            .order_by(Order.created_at.desc())
            .all()
        ):
            order_ids_by_user[uid].append(oid)

    items = [_user_item(u, n, spent, order_ids_by_user.get(u.id, [])) for u, n, spent in rows]
    return PaginatedResponse.create(items, total, page, page_size)


# =============================================================================
# 360º detail
# =============================================================================

@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user_detail(user_id: int, db: DbSession, admin_user: AdminUser):
    """Everything we know about a customer: profile, orders, coupons, reviews, cart, lead."""
    user = db.query(User).options(joinedload(User.addresses)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    orders = (
        db.query(Order).options(joinedload(Order.items))
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    paid = [o for o in orders if o.status in PAID_STATUSES]
    total_spent = sum((o.total for o in paid), 0)

    redemptions = (
        db.query(CouponRedemption).options(joinedload(CouponRedemption.order))
        .filter(CouponRedemption.user_id == user.id)
        .order_by(CouponRedemption.created_at.desc())
        .all()
    )
    reviews = (
        db.query(Review).options(joinedload(Review.product))
        .filter(Review.user_id == user.id)
        .order_by(Review.created_at.desc())
        .all()
    )
    cart = (
        db.query(Cart)
        .options(joinedload(Cart.items).joinedload(CartItem.product), joinedload(Cart.items).joinedload(CartItem.variant))
        .filter(Cart.user_id == user.id)
        .first()
    )
    lead = db.query(NewsletterLead).filter(NewsletterLead.email == user.email).first()

    cart_dict = None
    if cart and cart.items:
        cart_dict = {
            "id": cart.id,
            "coupon_code": cart.coupon_code,
            "item_count": cart.item_count,
            "subtotal": float(cart.subtotal),
            "created_at": cart.created_at,
            "updated_at": cart.updated_at,
            "items": [
                {
                    "product_name": it.product.name if it.product else "—",
                    "format": it.variant.format if it.variant else None,
                    "quantity": it.quantity,
                    "unit_price": float(it.price_at_add),
                    "total": float(it.total),
                }
                for it in cart.items
            ],
        }

    return AdminUserDetail(
        id=user.id, email=user.email, first_name=user.first_name, last_name=user.last_name,
        phone=user.phone, role=user.role, is_active=user.is_active,
        email_verified=user.email_verified, marketing_opt_in=user.marketing_opt_in,
        has_password=bool(user.password_hash), google_linked=bool(user.google_id),
        failed_login_attempts=user.failed_login_attempts or 0, locked_until=user.locked_until,
        last_login_at=user.last_login_at, login_count=user.login_count or 0,
        created_at=user.created_at, updated_at=user.updated_at,
        stats={
            "total_orders": len(orders),
            "paid_orders": len(paid),
            "total_spent": float(total_spent),
            "average_order_value": float(total_spent / len(paid)) if paid else 0.0,
            "first_order_at": orders[-1].created_at if orders else None,
            "last_order_at": orders[0].created_at if orders else None,
            "coupons_used": len([r for r in redemptions if not r.reverted_at]),
            "total_discount": float(sum((r.discount_amount for r in redemptions if not r.reverted_at), 0)),
            "reviews": len(reviews),
        },
        addresses=[
            {**a.to_dict(), "id": a.id, "label": a.label, "is_default": a.is_default}
            for a in user.addresses
        ],
        orders=[
            {
                "id": o.id, "order_number": o.order_number, "status": o.status,
                "total": float(o.total), "discount": float(o.discount or 0),
                "coupon_code": o.coupon_code, "item_count": o.item_count,
                "created_at": o.created_at, "paid_at": o.paid_at,
            }
            for o in orders
        ],
        coupon_redemptions=[
            {
                "id": r.id, "coupon_id": r.coupon_id, "coupon_code": r.coupon_code,
                "order_id": r.order_id, "order_number": r.order.order_number if r.order else None,
                "discount_amount": float(r.discount_amount), "reverted_at": r.reverted_at,
                "created_at": r.created_at,
            }
            for r in redemptions
        ],
        reviews=[
            {
                "id": r.id, "product_name": r.product.name if r.product else "—",
                "rating": r.rating, "title": r.title, "comment": r.comment,
                "status": r.status, "created_at": r.created_at,
            }
            for r in reviews
        ],
        cart=cart_dict,
        newsletter_lead=(
            {"source": lead.source, "coupon_code": lead.coupon_code,
             "created_at": lead.created_at, "converted_at": lead.converted_at}
            if lead else None
        ),
    )


# =============================================================================
# Edit
# =============================================================================

@router.patch("/users/{user_id}", response_model=Message)
def update_user(user_id: int, data: AdminUserUpdate, db: DbSession, admin_user: AdminUser):
    """Edit profile, role and flags of any account (admins included)."""
    user = _get_user_or_404(db, user_id)
    changes = data.model_dump(exclude_unset=True)

    _guard_admin_loss(
        db, admin_user, user,
        demote=user.role == "admin" and changes.get("role") == "customer",
        deactivate=user.is_active and changes.get("is_active") is False,
    )

    if "email" in changes and changes["email"]:
        new_email = changes["email"].lower()
        if new_email != user.email:
            if db.query(User).filter(User.email == new_email, User.id != user.id).first():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una cuenta con este email")
            user.email = new_email
        changes.pop("email")

    role_changed = "role" in changes and changes["role"] != user.role
    for field, value in changes.items():
        if value is not None or field == "phone":  # phone can be cleared
            setattr(user, field, (value or None) if field == "phone" else value)

    # Role or activation changes take effect immediately
    if role_changed or changes.get("is_active") is False:
        revoke_sessions(user)

    db.commit()
    logger.info("Admin %s updated user %s: %s", admin_user.email, user.id, sorted(changes.keys()))
    return Message(message="Usuario actualizado")


@router.patch("/users/{user_id}/status", response_model=Message)
def update_user_status(user_id: int, data: AdminUserStatusUpdate, db: DbSession, admin_user: AdminUser):
    """Activate / deactivate an account."""
    user = _get_user_or_404(db, user_id)
    _guard_admin_loss(db, admin_user, user, deactivate=user.is_active and not data.is_active)
    user.is_active = data.is_active
    if not data.is_active:
        revoke_sessions(user)
    db.commit()
    logger.info("Admin %s set user %s is_active=%s", admin_user.email, user.id, data.is_active)
    return Message(message="Cuenta activada" if data.is_active else "Cuenta desactivada")


@router.delete("/users/{user_id}", response_model=Message)
def delete_user(user_id: int, db: DbSession, admin_user: AdminUser):
    """Anonymise an account (RGPD). Orders are kept for fiscal records."""
    user = _get_user_or_404(db, user_id)
    _guard_admin_loss(db, admin_user, user, delete=True)
    anonymize_user(db, user)
    if user.role == "admin":
        user.role = "customer"
    db.commit()
    logger.info("Admin %s anonymised user %s", admin_user.email, user.id)
    return Message(message="Cuenta eliminada (datos personales anonimizados)")


# =============================================================================
# Security
# =============================================================================

@router.post("/users/{user_id}/password", response_model=Message)
def set_user_password(user_id: int, data: AdminSetPassword, db: DbSession, admin_user: AdminUser):
    """Set a new password directly. Closes every active session of that user."""
    user = _get_user_or_404(db, user_id)
    _reject_self(admin_user, user, "Cambia tu propia contraseña desde Mi cuenta")
    user.password_hash = get_password_hash(data.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    revoke_sessions(user)
    db.commit()
    logger.info("Admin %s set a new password for user %s", admin_user.email, user.id)

    if data.notify_user and user.is_active:
        if not EmailService.send_security_notification(user.email, user.first_name, "cambio de contraseña por un administrador"):
            logger.error("Security notification failed: user=%s", user.id)
    return Message(message="Contraseña actualizada. Se han cerrado sus sesiones activas.")


@router.post("/users/{user_id}/send-reset", response_model=Message)
def send_user_password_reset(user_id: int, db: DbSession, admin_user: AdminUser):
    """Email the user a password-reset link (valid 1 hour). Closes active sessions."""
    user = _get_user_or_404(db, user_id)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La cuenta está desactivada")

    token = generate_reset_token()
    db.add(PasswordResetToken(
        user_id=user.id, token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    if user.id != admin_user.id:
        revoke_sessions(user)
    db.commit()

    if not EmailService.send_password_reset_email(user.email, token):
        logger.error("Admin-triggered reset email failed: user=%s", user.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo enviar el email")
    logger.info("Admin %s sent password reset to user %s", admin_user.email, user.id)
    return Message(message=f"Email de restablecimiento enviado a {user.email}")


@router.post("/users/{user_id}/unlock", response_model=Message)
def unlock_user(user_id: int, db: DbSession, admin_user: AdminUser):
    """Clear the brute-force lockout."""
    user = _get_user_or_404(db, user_id)
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()
    return Message(message="Cuenta desbloqueada")


@router.post("/users/{user_id}/logout-all", response_model=Message)
def logout_user_everywhere(user_id: int, db: DbSession, admin_user: AdminUser):
    """Invalidate every active session of the user."""
    user = _get_user_or_404(db, user_id)
    _reject_self(admin_user, user, "Para cerrar tu sesión usa el botón de salir")
    revoke_sessions(user)
    db.commit()
    return Message(message="Sesiones cerradas")
