"""
Contact lead model — messages submitted from the /contacto page form.
"""
from datetime import datetime
from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime

from app.models.database import Base


class ContactLead(Base):
    """Lead captured from the /contacto contact form."""
    __tablename__ = "contact_leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    message = Column(Text, nullable=False)
    accepts_marketing = Column(Boolean, default=False, nullable=False)
    source = Column(String(50), default="contact_form", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ContactLead {self.email}>"
