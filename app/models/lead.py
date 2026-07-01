"""
Newsletter lead model — emails captured from the homepage popup
before the visitor registers a full account.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime

from app.models.database import Base


class NewsletterLead(Base):
    """Email captured from a marketing touchpoint (e.g. homepage popup)."""
    __tablename__ = "newsletter_leads"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    source = Column(String(50), default="homepage_popup", nullable=False)
    coupon_code = Column(String(50), nullable=True)
    converted_at = Column(DateTime, nullable=True)  # set when this email registers a user account
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<NewsletterLead {self.email}>"
