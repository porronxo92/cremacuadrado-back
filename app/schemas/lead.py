"""Lead capture schemas (newsletter popup, B2B landing forms)."""
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class NewsletterSubscribeRequest(BaseModel):
    email: EmailStr
    consent: bool = False  # casilla de comunicaciones comerciales (sin premarcar)
    source: Optional[str] = Field(None, max_length=50)


class NewsletterTokenRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=100)


class PosLeadRequest(BaseModel):
    """Payload from the /para-tiendas B2B landing page form."""
    name: str = Field(..., min_length=1, max_length=255)
    establishment_name: str = Field(..., min_length=1, max_length=255)
    city: str = Field(..., min_length=1, max_length=255)
    establishment_type: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    phone: str = Field(..., min_length=1, max_length=30)
    accept_privacy: bool = False  # información de privacidad leída (obligatoria)


class ContactFormRequest(BaseModel):
    """Payload from the /contacto contact form."""
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    message: str = Field(..., min_length=10, max_length=5000)
    accepts_marketing: bool = False
    accept_privacy: bool = False  # información de privacidad leída (obligatoria)
