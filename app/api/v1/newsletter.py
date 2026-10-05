"""
Newsletter — alta con doble opt-in (LSSI art. 21 y RGPD art. 7).

1. POST /subscribe: requiere consentimiento expreso (casilla sin premarcar).
   Guarda el lead con prueba del consentimiento y envía un email de confirmación.
   Responde siempre igual, exista o no el email (no revela quién está suscrito).
2. POST /confirm: con el token del email, activa la suscripción y envía el cupón.
3. POST /unsubscribe: baja con un clic desde cualquier email comercial.
"""
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import DbSession
from app.config import settings
from app.limiter import limiter
from app.models.lead import NewsletterLead
from app.models.order import Coupon
from app.models.user import User
from app.schemas.common import Message
from app.schemas.lead import NewsletterSubscribeRequest, NewsletterTokenRequest
from app.services import consents
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.newsletter")

router = APIRouter()

WELCOME_COUPON_CODE = "BIENVENIDO10"
_GENERIC_OK = "Revisa tu email y confirma la suscripción para recibir tu código de descuento"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unsubscribe_url(lead: NewsletterLead) -> str:
    return f"{settings.SITE_URL}/newsletter/baja?token={lead.unsubscribe_token}"


@router.post("/subscribe", response_model=Message, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def subscribe(request: Request, data: NewsletterSubscribeRequest, db: DbSession):
    """Alta pendiente de confirmar (doble opt-in)."""
    if not data.consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes aceptar recibir comunicaciones comerciales para suscribirte",
        )
    email = data.email.lower().strip()
    lead = db.query(NewsletterLead).filter(NewsletterLead.email == email).first()

    if lead and lead.confirmed_at and not lead.unsubscribed_at:
        # Ya suscrito: misma respuesta, sin reenviar nada
        logger.info("Newsletter subscribe: already confirmed")
        return Message(message=_GENERIC_OK)

    if lead is None:
        lead = NewsletterLead(email=email, source=data.source or "homepage_popup")
        db.add(lead)
    lead.consent_at = _utcnow()
    lead.confirm_token = secrets.token_urlsafe(32)
    lead.unsubscribe_token = lead.unsubscribe_token or secrets.token_urlsafe(32)
    lead.unsubscribed_at = None
    consents.record_consent(
        db, email=email, purpose=consents.NEWSLETTER, granted=True,
        source=data.source or "homepage_popup", request=request,
    )
    db.commit()

    confirm_url = f"{settings.SITE_URL}/newsletter/confirmar?token={lead.confirm_token}"
    if not EmailService.send_newsletter_confirmation_email(email, confirm_url):
        logger.error("Newsletter confirmation email failed")
    return Message(message=_GENERIC_OK)


@router.post("/confirm", response_model=Message)
@limiter.limit("10/minute")
async def confirm(request: Request, data: NewsletterTokenRequest, db: DbSession):
    """Confirma la suscripción (doble opt-in) y envía el cupón de bienvenida una sola vez."""
    lead = db.query(NewsletterLead).filter(NewsletterLead.confirm_token == data.token).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El enlace no es válido o ya se ha usado")

    first_time = lead.coupon_code is None
    lead.confirmed_at = _utcnow()
    lead.confirm_token = None
    if first_time:
        coupon = db.query(Coupon).filter(Coupon.code == WELCOME_COUPON_CODE).first()
        lead.coupon_code = coupon.code if coupon else WELCOME_COUPON_CODE
    db.commit()

    if first_time:
        if not EmailService.send_newsletter_welcome_email(lead.email, lead.coupon_code, unsubscribe_url(lead)):
            logger.error("Newsletter welcome email failed: lead=%s", lead.id)
        return Message(message="¡Suscripción confirmada! Te hemos enviado tu código de descuento por email.")
    return Message(message="¡Suscripción confirmada!")


@router.post("/unsubscribe", response_model=Message)
@limiter.limit("10/minute")
async def unsubscribe(request: Request, data: NewsletterTokenRequest, db: DbSession):
    """Baja de las comunicaciones comerciales (enlace incluido en cada email)."""
    lead = db.query(NewsletterLead).filter(NewsletterLead.unsubscribe_token == data.token).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El enlace de baja no es válido")
    if not lead.unsubscribed_at:
        lead.unsubscribed_at = _utcnow()
        consents.record_consent(
            db, email=lead.email, purpose=consents.NEWSLETTER, granted=False,
            source="unsubscribe_link", request=request,
        )
        user = db.query(User).filter(User.email == lead.email).first()
        if user and user.marketing_opt_in:
            user.marketing_opt_in = False
            consents.record_consent(
                db, email=user.email, purpose=consents.MARKETING, granted=False,
                source="unsubscribe_link", request=request, user_id=user.id,
            )
        db.commit()
    return Message(message="Te has dado de baja. No volverás a recibir comunicaciones comerciales.")
