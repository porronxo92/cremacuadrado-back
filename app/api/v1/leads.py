"""
B2B lead capture — landing page forms (/para-tiendas, /para-restaurantes).
Leads are stored in their own table; no external CRM integration.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import DbSession
from app.limiter import limiter
from app.models.pos_lead import PosLead
from app.schemas.lead import PosLeadRequest
from app.schemas.common import Message
from app.services import consents
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.leads")

router = APIRouter()


@router.post("/punto-de-venta", response_model=Message, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_pos_lead(request: Request, data: PosLeadRequest, db: DbSession):
    """Capture a lead from the /para-tiendas B2B form and notify both parties."""
    if not data.accept_privacy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Debes aceptar la información de privacidad")
    email = data.email.lower()

    lead = PosLead(
        name=data.name,
        establishment_name=data.establishment_name,
        city=data.city,
        establishment_type=data.establishment_type,
        email=email,
        phone=data.phone,
        privacy_accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(lead)
    consents.record_consent(db, email=email, purpose=consents.PRIVACY_B2B, granted=True,
                            source="para_tiendas", request=request)
    db.commit()

    sent = EmailService.send_pos_lead_confirmation_email(email, data.establishment_name)
    if not sent:
        logger.error("POS lead confirmation email failed: email=%s", email)

    notified = EmailService.send_admin_new_pos_lead(
        name=data.name,
        establishment_name=data.establishment_name,
        city=data.city,
        establishment_type=data.establishment_type,
        email=email,
        phone=data.phone,
    )
    if not notified:
        logger.error("POS lead admin notification failed: email=%s", email)

    logger.info("POS lead captured: email=%s establishment=%s", email, data.establishment_name)
    return Message(message="Solicitud recibida. Te contactamos en 48 horas.")
