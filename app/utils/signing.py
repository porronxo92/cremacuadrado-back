"""Enlaces firmados (HMAC) que no requieren iniciar sesión."""
import hashlib
import hmac

from app.config import settings


def invoice_token(order_number: str) -> str:
    """Token de descarga de la factura de un pedido (también para invitados)."""
    message = f"invoice:{order_number}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()[:40]


def verify_invoice_token(order_number: str, token: str) -> bool:
    return hmac.compare_digest(invoice_token(order_number), token or "")
