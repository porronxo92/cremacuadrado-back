"""Shared account operations used by self-service and admin endpoints."""
import uuid

from app.models.user import User


def anonymize_user(user: User) -> None:
    """
    Strip PII and deactivate an account (RGPD right to erasure).
    Orders are preserved for legal/fiscal records. Caller commits.
    """
    user.email = f"deleted_{uuid.uuid4().hex[:8]}@cremacuadrado.invalid"
    user.first_name = "Usuario"
    user.last_name = "Eliminado"
    user.phone = None
    user.google_id = None
    user.password_hash = None
    user.is_active = False
    user.marketing_opt_in = False
    user.token_version = (user.token_version or 0) + 1


def revoke_sessions(user: User) -> None:
    """Invalidate every JWT previously issued to this user. Caller commits."""
    user.token_version = (user.token_version or 0) + 1
