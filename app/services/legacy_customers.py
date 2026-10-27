"""
Clientes históricos de la web antigua: cupón personal del 20 % en todos sus pedidos
durante el periodo de la promoción (legacy_program, por defecto del 28/10/2026 al
27/10/2027; editable desde /admin/cupones). No es acumulable: un cupón por pedido.

El cupón se crea cuando la cuenta tiene el email VERIFICADO (verificación por enlace,
Google o login de una cuenta ya verificada). SEGURIDAD: sin verificar, cualquiera podría
registrarse con el email de un cliente histórico y quedarse con su descuento.
"""
import logging
import secrets
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.legacy_customer import LegacyCustomer, LegacyProgram
from app.models.order import Coupon
from app.models.user import User
from app.services.email import EmailService

logger = logging.getLogger("cremacuadrado.legacy_customers")

LEGACY_DISCOUNT_PERCENT = Decimal("20")
DEFAULT_STARTS_ON = date(2026, 10, 28)
DEFAULT_ENDS_ON = date(2027, 10, 27)
_CODE_PREFIX = "CLIENTE20-"
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I
_MADRID = ZoneInfo("Europe/Madrid")


def find_legacy_customer(db: Session, email: str | None) -> LegacyCustomer | None:
    if not email:
        return None
    try:
        return db.query(LegacyCustomer).filter(LegacyCustomer.email == email.strip().lower()).first()
    except SQLAlchemyError:
        # Tabla sin migrar: el registro sigue con el flujo normal
        db.rollback()
        logger.exception("Legacy customers lookup failed")
        return None


def get_program(db: Session) -> LegacyProgram:
    """Fila única con el periodo; si falta (BBDD de tests) se crea con las fechas por defecto."""
    program = db.query(LegacyProgram).filter(LegacyProgram.id == 1).first()
    if program is None:
        program = LegacyProgram(id=1, starts_on=DEFAULT_STARTS_ON, ends_on=DEFAULT_ENDS_ON)
        db.add(program)
        db.flush()
    return program


def _utc_naive(day: date, at: time) -> datetime:
    """Hora peninsular de ese día → UTC naive, como guarda Coupon.valid_from/valid_until."""
    return datetime.combine(day, at, tzinfo=_MADRID).astimezone(timezone.utc).replace(tzinfo=None)


def madrid_day(dt: datetime | None) -> date | None:
    """UTC naive de la BBDD → día en hora peninsular."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(_MADRID).date()


def coupon_period(program: LegacyProgram) -> tuple[datetime, datetime]:
    """Desde las 00:00 del primer día hasta las 23:59:59 del último (hora peninsular)."""
    return _utc_naive(program.starts_on, time(0, 0)), _utc_naive(program.ends_on, time(23, 59, 59))


def update_program(db: Session, starts_on: date, ends_on: date) -> int:
    """Cambia el periodo y lo aplica a todos los cupones ya concedidos. Devuelve cuántos. No hace commit."""
    program = get_program(db)
    program.starts_on, program.ends_on = starts_on, ends_on
    valid_from, valid_until = coupon_period(program)
    coupon_ids = db.query(LegacyCustomer.coupon_id).filter(LegacyCustomer.coupon_id.isnot(None))
    return db.query(Coupon).filter(Coupon.id.in_(coupon_ids)).update(
        {Coupon.valid_from: valid_from, Coupon.valid_until: valid_until}, synchronize_session=False,
    )


def program_stats(db: Session) -> dict:
    total, granted = db.query(func.count(LegacyCustomer.id), func.count(LegacyCustomer.coupon_id)).one()
    return {"customers": total, "granted": granted}


def legacy_coupon_for(db: Session, user: User) -> Coupon | None:
    """Cupón de cliente histórico de esta cuenta (para «Mi cuenta → Perfil»)."""
    try:
        return (
            db.query(Coupon)
            .join(LegacyCustomer, LegacyCustomer.coupon_id == Coupon.id)
            .filter(LegacyCustomer.user_id == user.id, Coupon.user_id == user.id, Coupon.is_active.is_(True))
            .first()
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Legacy coupon lookup failed: user_id=%s", user.id)
        return None


def _new_code(db: Session) -> str:
    while True:
        code = _CODE_PREFIX + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if not db.query(Coupon.id).filter(Coupon.code == code).first():
            return code


def grant_legacy_benefit(db: Session, user: User) -> None:
    """
    Si el email verificado de la cuenta está en legacy_customers, crea su cupón personal
    (20 %, reutilizable, con el periodo de la promoción) y le envía la bienvenida especial.
    Idempotente: el cupón se crea una vez y el email se reintenta hasta que sale.
    Llamar después del commit del endpoint: hace sus propios commits y nunca propaga errores.
    """
    if not user or not user.email_verified or not user.is_active:
        return
    legacy = find_legacy_customer(db, user.email)
    if not legacy or legacy.welcome_sent_at:
        return
    if legacy.user_id is not None and legacy.user_id != user.id:
        return  # ya lo reclamó otra cuenta con ese email (p. ej. anonimizada)

    try:
        if legacy.coupon_id is None:
            valid_from, valid_until = coupon_period(get_program(db))
            coupon = Coupon(
                code=_new_code(db),
                description=f"Cliente histórico: 20 % en todos sus pedidos ({user.email})"[:255],
                discount_type="percent",
                discount_value=LEGACY_DISCOUNT_PERCENT,
                min_order_amount=Decimal("0"),
                valid_from=valid_from,
                valid_until=valid_until,
                is_active=True,
                user_id=user.id,
                reusable=True,
            )
            db.add(coupon)
            db.flush()
            legacy.coupon_id = coupon.id
            legacy.user_id = user.id
            legacy.benefit_granted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            logger.info("Legacy benefit granted: user_id=%s coupon=%s", user.id, coupon.code)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Legacy benefit not granted: user_id=%s", user.id)
        return

    coupon = legacy.coupon
    if coupon is None:
        return
    first_name = user.first_name or legacy.first_name or ""
    if EmailService.send_legacy_welcome_email(user.email, first_name, coupon.code, coupon.valid_from, coupon.valid_until):
        legacy.welcome_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("Legacy welcome email sent: user_id=%s", user.id)
    else:
        logger.error("Legacy welcome email failed: user_id=%s", user.id)
