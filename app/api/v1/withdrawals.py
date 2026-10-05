"""
Derecho de desistimiento (TRLGDCU arts. 102-108) y "botón de desistimiento"
(Directiva (UE) 2023/2673, art. 11 bis Directiva 2011/83/UE).

Endpoint público: funciona para clientes registrados y para invitados
identificándose con el número de pedido y el email de la compra. Envía un acuse
de recibo en soporte duradero (email) y avisa al equipo.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import DbSession
from app.limiter import limiter
from app.models.compliance import WithdrawalRequest
from app.models.order import Order
from app.services import consents
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.withdrawals")

router = APIRouter()

WITHDRAWAL_DAYS = 14
_CANCELLABLE = {"paid", "processing", "shipped", "delivered", "partially_refunded"}


class WithdrawalCreate(BaseModel):
    order_number: str = Field(..., min_length=5, max_length=20)
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    items_text: str | None = Field(None, max_length=2000)  # vacío = pedido completo
    reason: str | None = Field(None, max_length=2000)      # opcional


class WithdrawalReceipt(BaseModel):
    reference: str
    order_number: str
    requested_at: datetime
    within_term: bool
    message: str


def _within_term(order: Order, now: datetime) -> bool:
    """14 días naturales desde la entrega. Antes de entregarse, siempre en plazo."""
    if not order.delivered_at:
        return True
    delivered = order.delivered_at
    if delivered.tzinfo is not None:
        delivered = delivered.astimezone(timezone.utc).replace(tzinfo=None)
    return now <= delivered + timedelta(days=WITHDRAWAL_DAYS)


@router.post("", response_model=WithdrawalReceipt, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def request_withdrawal(request: Request, data: WithdrawalCreate, db: DbSession):
    email = data.email.lower().strip()
    order = db.query(Order).filter(Order.order_number == data.order_number.strip().upper()).first()
    # Mismo error si no existe o el email no coincide: no revela pedidos ajenos
    if not order or (order.customer_email or "").lower() != email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No encontramos un pedido con ese número y ese email",
        )
    if order.status not in _CANCELLABLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este pedido no admite desistimiento (no está pagado o ya está cancelado/reembolsado)",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    withdrawal = WithdrawalRequest(
        order_id=order.id,
        email=email,
        full_name=data.full_name.strip(),
        items_text=(data.items_text or "").strip() or None,
        reason=(data.reason or "").strip() or None,
        within_term=_within_term(order, now),
        ip=consents.client_ip(request),
        requested_at=now,
    )
    db.add(withdrawal)
    db.commit()

    if EmailService.send_withdrawal_ack(email, withdrawal.full_name, order.order_number,
                                        withdrawal.id, withdrawal.items_text, now):
        withdrawal.ack_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    else:
        logger.error("Withdrawal ack email failed: withdrawal=%s", withdrawal.id)
    if not EmailService.send_admin_withdrawal(order.order_number, email, withdrawal.full_name,
                                              withdrawal.items_text, withdrawal.reason, withdrawal.within_term):
        logger.error("Withdrawal admin notification failed: withdrawal=%s", withdrawal.id)

    logger.info("Withdrawal requested: withdrawal=%s order=%s within_term=%s",
                withdrawal.id, order.order_number, withdrawal.within_term)
    return WithdrawalReceipt(
        reference=f"D-{withdrawal.id:06d}",
        order_number=order.order_number,
        requested_at=now,
        within_term=withdrawal.within_term,
        message="Hemos recibido tu solicitud de desistimiento. Te hemos enviado el acuse de recibo por email.",
    )
