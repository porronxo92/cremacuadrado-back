"""
B2B lead capture — landing page forms (/para-tiendas, /para-restaurantes).
Leads are stored in their own table; no external CRM integration.
"""
import logging

from fastapi import APIRouter, Request, status

from app.api.deps import DbSession
from app.limiter import limiter
from app.models.pos_lead import PosLead
from app.schemas.lead import PosLeadRequest
from app.schemas.common import Message
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.leads")

router = APIRouter()


@router.post("/punto-de-venta", response_model=Message, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_pos_lead(request: Request, data: PosLeadRequest, db: DbSession):
    """Capture a lead from the /para-tiendas B2B form and notify both parties."""
    email = data.email.lower()

    lead = PosLead(
        name=data.name,
        establishment_name=data.establishment_name,
        city=data.city,
        establishment_type=data.establishment_type,
        email=email,
        phone=data.phone,
    )
    db.add(lead)
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
