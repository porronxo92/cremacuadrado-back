"""Shared account operations used by self-service and admin endpoints."""
import uuid

from sqlalchemy.orm import Session

from app.models.cart import Cart
from app.models.compliance import ConsentRecord
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.pos_lead import PosLead
from app.models.user import Address, EmailVerificationToken, PasswordResetToken, User


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

    if original_email:
        db.query(NewsletterLead).filter(NewsletterLead.email == original_email).delete(synchronize_session=False)
        db.query(ContactLead).filter(ContactLead.email == original_email).delete(synchronize_session=False)
        db.query(PosLead).filter(PosLead.email == original_email).delete(synchronize_session=False)
        # El rastro de consentimientos se mantiene, pero ligado a la cuenta anonimizada
        db.query(ConsentRecord).filter(ConsentRecord.subject_email == original_email).update(
            {ConsentRecord.user_id: user.id}, synchronize_session=False,
        )

    # Las reseñas se mantienen (contenido publicado) pero sin nombre: el API
    # muestra el first_name del usuario, que ya es "Usuario".


def revoke_sessions(user: User) -> None:
    """Invalidate every JWT previously issued to this user. Caller commits."""
    user.token_version = (user.token_version or 0) + 1
