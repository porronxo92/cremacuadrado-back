"""
Contact form endpoint — /contacto page submissions.
Saves the message as a ContactLead and notifies info@cremacuadrado.com.
"""
import logging

from fastapi import APIRouter, Request, status

from app.api.deps import DbSession
from app.limiter import limiter
from app.models.contact_lead import ContactLead
from app.schemas.lead import ContactFormRequest
from app.schemas.common import Message
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.contact")

router = APIRouter()


@router.post("/submit", response_model=Message, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def submit_contact_form(request: Request, data: ContactFormRequest, db: DbSession):
    """Save a contact form submission and send notification emails to both parties."""
    email = data.email.lower().strip()

    lead = ContactLead(
        name=data.name.strip(),
        email=email,
        message=data.message.strip(),
        accepts_marketing=data.accepts_marketing,
    )
    db.add(lead)
    db.commit()

    # Confirmation to the sender
    confirmed = EmailService.send_contact_form_confirmation(email, data.name.strip())
    if not confirmed:
        logger.error("Contact form confirmation email failed: email=%s", email)

    # Notification to admin
    notified = EmailService.send_admin_contact_form(
        name=data.name.strip(),
        email=email,
        message=data.message.strip(),
        accepts_marketing=data.accepts_marketing,
    )
    if not notified:
        logger.error("Contact form admin notification failed: email=%s", email)

    logger.info("Contact form submitted: email=%s accepts_marketing=%s", email, data.accepts_marketing)
    return Message(message="Mensaje enviado. Te responderemos lo antes posible.")
