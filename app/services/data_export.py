"""
Exportación de los datos personales de un usuario (RGPD arts. 15 y 20).
Devuelve un dict serializable a JSON con todo lo que se guarda sobre la cuenta.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.compliance import ConsentRecord, WithdrawalRequest
from app.models.contact_lead import ContactLead
from app.models.invoice import Invoice
from app.models.lead import NewsletterLead
from app.models.order import Order
from app.models.pos_lead import PosLead
from app.models.product import Review
from app.models.user import Address, User


def _v(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row(obj, fields: list[str]) -> dict:
    return {f: _v(getattr(obj, f, None)) for f in fields}


def export_user_data(db: Session, user: User) -> dict:
    email = user.email.lower()
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at).all()
    order_ids = [o.id for o in orders]

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "controller": "CREMACUADRADO SL (B56673700) · info@cremacuadrado.com",
        "account": _row(user, [
            "email", "first_name", "last_name", "phone", "role", "email_verified",
            "marketing_opt_in", "created_at", "last_login_at", "login_count",
        ]) | {"google_account_linked": bool(user.google_id)},
        "addresses": [
            _row(a, ["label", "first_name", "last_name", "street", "street_2", "city", "province",
                     "postal_code", "country", "phone", "is_default", "created_at"])
            for a in db.query(Address).filter(Address.user_id == user.id).all()
        ],
        "orders": [
            _row(o, ["order_number", "status", "subtotal", "shipping_cost", "discount", "tax", "total",
                     "coupon_code", "payment_method", "tracking_number", "customer_notes",
                     "terms_version", "terms_accepted_at", "created_at", "paid_at", "shipped_at", "delivered_at"])
            | {
                "shipping_address": o.shipping_address,
                "billing_address": o.billing_address,
                "items": [_row(i, ["product_name", "product_sku", "quantity", "unit_price", "total"]) for i in o.items],
            }
            for o in orders
        ],
        "invoices": [
            _row(inv, ["invoice_number", "invoice_type", "issued_at", "tax_base", "tax_amount", "total"])
            | {"buyer": inv.buyer_snapshot}
            for inv in (db.query(Invoice).filter(Invoice.order_id.in_(order_ids)).all() if order_ids else [])
        ],
        "withdrawals": [
            _row(w, ["email", "full_name", "items_text", "reason", "status", "requested_at"])
            for w in (db.query(WithdrawalRequest).filter(WithdrawalRequest.order_id.in_(order_ids)).all()
                      if order_ids else [])
        ],
        "reviews": [
            _row(r, ["rating", "title", "comment", "status", "created_at"]) | {"product_id": r.product_id}
            for r in db.query(Review).filter(Review.user_id == user.id).all()
        ],
        "newsletter": [
            _row(n, ["email", "source", "coupon_code", "consent_at", "confirmed_at", "unsubscribed_at", "created_at"])
            for n in db.query(NewsletterLead).filter(NewsletterLead.email == email).all()
        ],
        "contact_messages": [
            _row(c, ["name", "email", "message", "accepts_marketing", "created_at"])
            for c in db.query(ContactLead).filter(ContactLead.email == email).all()
        ],
        "b2b_requests": [
            _row(p, ["name", "establishment_name", "city", "establishment_type", "email", "phone", "created_at"])
            for p in db.query(PosLead).filter(PosLead.email == email).all()
        ],
        "consents": [
            _row(c, ["purpose", "granted", "policy_version", "source", "created_at"])
            for c in db.query(ConsentRecord)
            .filter((ConsentRecord.subject_email == email) | (ConsentRecord.user_id == user.id))
            .order_by(ConsentRecord.created_at).all()
        ],
    }
