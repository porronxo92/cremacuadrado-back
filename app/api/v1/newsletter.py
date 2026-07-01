"""
Newsletter lead capture — homepage popup email signup.
Stores the email even if the visitor never completes registration, so it can
be used later for a "finish signing up" reminder, and sends the welcome coupon.
"""
import logging

from fastapi import APIRouter, Request, status

from app.api.deps import DbSession
from app.limiter import limiter
from app.models.lead import NewsletterLead
from app.models.order import Coupon
from app.schemas.lead import NewsletterSubscribeRequest
from app.schemas.common import Message
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.newsletter")

router = APIRouter()

WELCOME_COUPON_CODE = "BIENVENIDO10"


@router.post("/subscribe", response_model=Message, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def subscribe(request: Request, data: NewsletterSubscribeRequest, db: DbSession):
    """Capture a lead email (e.g. from the homepage popup) and send the welcome coupon."""
    email = data.email.lower()

    existing = db.query(NewsletterLead).filter(NewsletterLead.email == email).first()
    if existing:
        logger.info("Newsletter subscribe: email already captured email=%s", email)
        return Message(message="Ya tienes tu código de descuento en el email")

    coupon = db.query(Coupon).filter(Coupon.code == WELCOME_COUPON_CODE).first()
    coupon_code = coupon.code if coupon else WELCOME_COUPON_CODE

    lead = NewsletterLead(email=email, source="homepage_popup", coupon_code=coupon_code)
    db.add(lead)
    db.commit()

    sent = EmailService.send_newsletter_welcome_email(email, coupon_code)
    if not sent:
        logger.error("Newsletter welcome email failed: email=%s", email)

    logger.info("Newsletter lead captured: email=%s coupon=%s", email, coupon_code)
    return Message(message="Revisa tu email para ver tu código de descuento")
