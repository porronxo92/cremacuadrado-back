"""
Limitación del plazo de conservación (RGPD art. 5.1.e).

Purga periódica (Vercel Cron diario → GET /api/v1/maintenance/purge). Los plazos
están en app/config.py (RETENTION_*) y deben coincidir con lo que dice la
política de privacidad. No se tocan pedidos ni facturas (obligación legal) ni
consent_records (prueba del consentimiento, RGPD art. 7.1).
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.cart import Cart
from app.models.compliance import AdminAuditLog
from app.models.contact_lead import ContactLead
from app.models.lead import NewsletterLead
from app.models.payment import StripeWebhookEvent
from app.models.pos_lead import PosLead
from app.models.user import EmailVerificationToken, PasswordResetToken

logger = logging.getLogger("cremacuadrado.retention")


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def purge(db: Session) -> dict[str, int]:
    """Borra lo que ha superado su plazo de conservación. Hace commit."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    counts: dict[str, int] = {}

    # Carritos de invitado abandonados (los items caen por ON DELETE CASCADE)
    counts["guest_carts"] = db.query(Cart).filter(
        Cart.user_id.is_(None), Cart.updated_at < _ago(settings.RETENTION_GUEST_CART_DAYS),
    ).delete(synchronize_session=False)

    # Newsletter: altas sin confirmar (doble opt-in) y bajas
    counts["newsletter_unconfirmed"] = db.query(NewsletterLead).filter(
        NewsletterLead.confirmed_at.is_(None),
        NewsletterLead.converted_at.is_(None),
        NewsletterLead.created_at < _ago(settings.RETENTION_UNCONFIRMED_LEAD_DAYS),
    ).delete(synchronize_session=False)
    counts["newsletter_unsubscribed"] = db.query(NewsletterLead).filter(
        NewsletterLead.unsubscribed_at.isnot(None),
        NewsletterLead.unsubscribed_at < _ago(settings.RETENTION_UNCONFIRMED_LEAD_DAYS),
    ).delete(synchronize_session=False)
    # Los suscriptores confirmados se conservan hasta que se den de baja.

    # Leads B2B y mensajes de contacto
    counts["pos_leads"] = db.query(PosLead).filter(
        PosLead.created_at < _ago(settings.RETENTION_LEAD_DAYS),
    ).delete(synchronize_session=False)
    counts["contact_leads"] = db.query(ContactLead).filter(
        ContactLead.created_at < _ago(settings.RETENTION_CONTACT_DAYS),
    ).delete(synchronize_session=False)

    # Tokens caducados o usados
    counts["password_reset_tokens"] = db.query(PasswordResetToken).filter(
        or_(PasswordResetToken.expires_at < now, PasswordResetToken.used.is_(True)),
    ).delete(synchronize_session=False)
    counts["email_verification_tokens"] = db.query(EmailVerificationToken).filter(
        or_(EmailVerificationToken.expires_at < now, EmailVerificationToken.used.is_(True)),
    ).delete(synchronize_session=False)

    # Eventos de Stripe ya procesados (contienen emails y direcciones)
    counts["stripe_webhook_events"] = db.query(StripeWebhookEvent).filter(
        and_(StripeWebhookEvent.processed.is_(True),
             StripeWebhookEvent.created_at < _ago(settings.RETENTION_WEBHOOK_EVENT_DAYS)),
    ).delete(synchronize_session=False)

    # Registro de accesos del admin
    counts["admin_audit_log"] = db.query(AdminAuditLog).filter(
        AdminAuditLog.created_at < _ago(settings.RETENTION_AUDIT_LOG_DAYS),
    ).delete(synchronize_session=False)

    db.commit()
    logger.info("Retention purge done: %s", counts)
    return counts
