"""Lead capture schemas (newsletter popup, B2B landing forms)."""
from pydantic import BaseModel, EmailStr, Field


class NewsletterSubscribeRequest(BaseModel):
    email: EmailStr


class PosLeadRequest(BaseModel):
    """Payload from the /para-tiendas B2B landing page form."""
    name: str = Field(..., min_length=1, max_length=255)
    establishment_name: str = Field(..., min_length=1, max_length=255)
    city: str = Field(..., min_length=1, max_length=255)
    establishment_type: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    phone: str = Field(..., min_length=1, max_length=30)
