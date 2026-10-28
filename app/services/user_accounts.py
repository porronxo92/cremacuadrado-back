"""Shared account operations used by self-service and admin endpoints."""
import logging
import uuid

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.cart import Cart
from app.models.compliance import ConsentRecord
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.legacy_customer import LegacyCustomer
from app.models.invoice import Invoice
from app.models.order import Coupon, Order
from app.models.pos_lead import PosLead
from app.models.user import Address, EmailVerificationToken, PasswordResetToken, User

logger = logging.getLogger("cremacuadrado.accounts")


def link_guest_orders(db: Session, user: User) -> int:
    """
    Vincula a la cuenta los pedidos de invitado hechos con su mismo email.

    SEGURIDAD: solo si el email está verificado (`email_verified`); si no, cualquiera
    podría registrarse con el email de otra persona y ver sus pedidos y direcciones.
    Idempotente (solo toca `user_id IS NULL`). Las facturas no se modifican: guardan
    su propia copia de los datos. No hace commit; devuelve nº de pedidos vinculados.
    Nunca propaga errores de BD: los UPDATE van en un savepoint (todo o nada) y, si fallan, devuelve 0.
    """
    if not user or not user.email_verified or not user.is_active or not user.email:
        return 0
    email = user.email.lower()
    order_ids = [
        oid for (oid,) in db.query(Order.id).filter(
            Order.user_id.is_(None), func.lower(Order.guest_email) == email,
        ).all()
    ]
    if not order_ids:
        return 0
    try:
        # Savepoint: si falla cualquier UPDATE se deshacen ambos y la sesión sigue usable
        with db.begin_nested():
            db.query(Order).filter(Order.id.in_(order_ids), Order.user_id.is_(None)).update(
                {Order.user_id: user.id}, synchronize_session=False,
            )
            # invoices.user_id es una copia aparte; el trigger de inmutabilidad permite
            # NULL -> valor y valor -> NULL, pero no valor -> otro valor
            db.query(Invoice).filter(Invoice.order_id.in_(order_ids), Invoice.user_id.is_(None)).update(
                {Invoice.user_id: user.id}, synchronize_session=False,
            )
    except SQLAlchemyError:
        logger.exception("Guest orders link failed: user_id=%s orders=%s", user.id, len(order_ids))
        return 0
    linked = len(order_ids)
    logger.info("Guest orders linked: user_id=%s count=%s", user.id, linked)
    return linked


def anonymize_user(db: Session, user: User) -> None:
    """
    Supresión (RGPD art. 17): borra o anonimiza los datos personales de la cuenta.

    Se CONSERVAN, bloqueados, los pedidos y las facturas (obligación legal: Código
    de Comercio art. 30 y LGT art. 66 — 6 y 4 años). Las facturas guardan su
    propia copia de los datos fiscales y no se modifican. También se conservan
    las filas de consent_records, que son la prueba exigida por el art. 7.1 RGPD.
    Caller commits.
    """
    original_email = (user.email or "").lower()

    # Un pedido vinculado desde invitado conserva guest_email: se limpia para que, si
    # alguna vez la FK se pusiera a NULL, no se revinculara a otra persona con ese email.
    # (Las facturas guardan su propia copia de los datos fiscales.)
    db.query(Order).filter(Order.user_id == user.id, Order.guest_email.isnot(None)).update(
        {Order.guest_email: None}, synchronize_session=False,
    )

    # Datos de la cuenta
    user.email = f"deleted_{uuid.uuid4().hex[:8]}@cremacuadrado.invalid"
    user.first_name = "Usuario"
    user.last_name = "Eliminado"
    user.phone = None
    user.google_id = None
    user.password_hash = None
    user.is_active = False
    user.marketing_opt_in = False
    user.token_version = (user.token_version or 0) + 1

    # Datos asociados que no hay obligación de conservar
    db.query(Address).filter(Address.user_id == user.id).delete(synchronize_session=False)
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete(synchronize_session=False)
    db.query(EmailVerificationToken).filter(EmailVerificationToken.user_id == user.id).delete(synchronize_session=False)
    for cart in db.query(Cart).filter(Cart.user_id == user.id).all():
        db.delete(cart)
    # Cupones personales (cliente histórico): se desactivan; los pedidos guardan su propio código
    db.query(Coupon).filter(Coupon.user_id == user.id).update(
        {Coupon.is_active: False}, synchronize_session=False,
    )

    if original_email:
        db.query(NewsletterLead).filter(NewsletterLead.email == original_email).delete(synchronize_session=False)
        db.query(ContactLead).filter(ContactLead.email == original_email).delete(synchronize_session=False)
        db.query(PosLead).filter(PosLead.email == original_email).delete(synchronize_session=False)
        db.query(LegacyCustomer).filter(LegacyCustomer.email == original_email).delete(synchronize_session=False)
        # El rastro de consentimientos se mantiene, pero ligado a la cuenta anonimizada
        db.query(ConsentRecord).filter(ConsentRecord.subject_email == original_email).update(
            {ConsentRecord.user_id: user.id}, synchronize_session=False,
        )

    # Las reseñas se mantienen (contenido publicado) pero sin nombre: el API
    # muestra el first_name del usuario, que ya es "Usuario".


def revoke_sessions(user: User) -> None:
    """Invalidate every JWT previously issued to this user. Caller commits."""
    user.token_version = (user.token_version or 0) + 1
