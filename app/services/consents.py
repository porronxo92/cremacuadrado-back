"""
Registro de consentimientos (RGPD art. 7.1: el responsable debe poder demostrar
que el interesado consintió). Cada alta o baja se guarda como una fila nueva,
nunca se sobrescribe: el estado vigente es la última fila por email y finalidad.
"""
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.limiter import _get_real_ip
from app.models.compliance import ConsentRecord

# Finalidades
NEWSLETTER = "newsletter"            # envío de comunicaciones comerciales (LSSI art. 21)
MARKETING = "marketing"              # comunicaciones comerciales a clientes registrados
PRIVACY_CONTACT = "privacy_contact"  # información de privacidad aceptada en /contacto
PRIVACY_B2B = "privacy_b2b"          # información de privacidad aceptada en formularios B2B
TERMS_REGISTER = "terms_register"    # condiciones y privacidad aceptadas al registrarse


def client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    ip = _get_real_ip(request)
    return ip[:45] if ip and ip != "unknown" else None


def record_consent(
    db: Session,
    *,
    email: str,
    purpose: str,
    granted: bool,
    source: str,
    request: Optional[Request] = None,
    user_id: Optional[int] = None,
    policy_version: Optional[str] = None,
) -> ConsentRecord:
    """Añade una fila de consentimiento. No hace commit."""
    user_agent = request.headers.get("user-agent", "")[:255] if request is not None else None
    record = ConsentRecord(
        subject_email=email.lower().strip(),
        user_id=user_id,
        purpose=purpose,
        granted=granted,
        policy_version=policy_version or settings.PRIVACY_VERSION,
        source=source,
        ip=client_ip(request),
        user_agent=user_agent or None,
    )
    db.add(record)
    return record


def latest(db: Session, email: str, purpose: str) -> Optional[ConsentRecord]:
    return (
        db.query(ConsentRecord)
        .filter(ConsentRecord.subject_email == email.lower().strip(), ConsentRecord.purpose == purpose)
        .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
        .first()
    )
