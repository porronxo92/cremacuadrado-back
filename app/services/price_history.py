"""
Historial de precios por variante (Directiva Omnibus, TRLGDCU art. 20.2).

Cualquier cambio de ProductVariant.price — desde el API, SQLAdmin o un script —
abre un nuevo periodo en price_history gracias a los eventos de SQLAlchemy.

Para anunciar una rebaja hay que indicar el precio más bajo aplicado en los
30 días anteriores a la rebaja. `prior_lowest_price()` devuelve ese precio solo
si es mayor que el actual; si no, no hay rebaja que anunciar y devuelve None.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, event, func, insert, or_, update
from sqlalchemy.orm import Session, object_session

from app.models.compliance import PriceHistory
from app.models.product import ProductVariant

WINDOW_DAYS = 30
_ph = PriceHistory.__table__


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(ProductVariant, "after_insert")
def _open_history(mapper, connection, target: ProductVariant) -> None:
    connection.execute(insert(_ph).values(variant_id=target.id, price=target.price, valid_from=_now()))


@event.listens_for(ProductVariant, "after_update")
def _record_price_change(mapper, connection, target: ProductVariant) -> None:
    from sqlalchemy import inspect
    history = inspect(target).attrs.price.history
    if not history.has_changes():
        return
    old = history.deleted[0] if history.deleted else None
    if old is not None and Decimal(str(old)) == Decimal(str(target.price)):
        return
    now = _now()
    connection.execute(
        update(_ph).where(and_(_ph.c.variant_id == target.id, _ph.c.valid_to.is_(None))).values(valid_to=now)
    )
    connection.execute(insert(_ph).values(variant_id=target.id, price=target.price, valid_from=now))


def prior_lowest_price(db: Session, variant: ProductVariant) -> Optional[Decimal]:
    """Precio más bajo de los 30 días previos al precio actual, si el actual es una rebaja."""
    current = (
        db.query(PriceHistory)
        .filter(PriceHistory.variant_id == variant.id, PriceHistory.valid_to.is_(None))
        .first()
    )
    if current is None:
        return None
    window_start = current.valid_from - timedelta(days=WINDOW_DAYS)
    lowest = (
        db.query(func.min(PriceHistory.price))
        .filter(
            PriceHistory.variant_id == variant.id,
            PriceHistory.valid_from < current.valid_from,
            or_(PriceHistory.valid_to.is_(None), PriceHistory.valid_to > window_start),
        )
        .scalar()
    )
    if lowest is None or Decimal(str(lowest)) <= Decimal(str(variant.price)):
        return None
    return Decimal(str(lowest))


def prior_lowest_for(variant: ProductVariant) -> Optional[Decimal]:
    db = object_session(variant)
    return prior_lowest_price(db, variant) if db is not None else None
