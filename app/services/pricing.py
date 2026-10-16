"""
Cálculo único de totales (descuento de cupón, envío e importe final).

Lo usan el carrito, el coste de envío, la validación del checkout y, por tanto,
el PaymentIntent: lo que se muestra es lo que se cobra. Función pura, sin BD.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app.config import settings

CENT = Decimal("0.01")
FREE_SHIPPING_TYPE = "free_shipping"


def format_eur(amount) -> str:
    """Importe en formato español: 7,95 € / 1.234,50 €."""
    value = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €"


def _threshold() -> Decimal:
    return Decimal(str(settings.FREE_SHIPPING_THRESHOLD))


def _shipping_base(amount_after_discount: Decimal) -> Decimal:
    """Envío según el umbral de envío gratis (sobre subtotal - descuento)."""
    if amount_after_discount >= _threshold():
        return Decimal("0")
    return Decimal(str(settings.SHIPPING_COST))


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal
    discount: Decimal           # solo productos
    shipping_base: Decimal      # envío antes de cupón
    shipping_discount: Decimal  # envío anulado por cupón free_shipping
    shipping_cost: Decimal      # envío final
    total: Decimal
    shipping_message: str
    coupon_applies: bool
    coupon_message: Optional[str]

    @property
    def coupon_amount(self) -> Decimal:
        """Descuento efectivo del cupón (productos o envío anulado)."""
        return self.discount if self.discount > 0 else self.shipping_discount


def _inapplicable(coupon, subtotal: Decimal, for_apply: bool) -> Optional[str]:
    """Motivo por el que el cupón no aplicaría a este subtotal (None si aplica)."""
    min_amount = Decimal(str(coupon.min_order_amount or 0))
    if subtotal < min_amount:
        return (
            f"Añade {format_eur(min_amount - subtotal)} más para usar el cupón "
            f"{coupon.code} (pedido mínimo {format_eur(min_amount)})."
        )
    if coupon.discount_type == FREE_SHIPPING_TYPE and _shipping_base(subtotal) == 0:
        if for_apply:
            return (
                f"Tu pedido ya tiene envío gratis (desde {format_eur(_threshold())}). "
                f"Guarda el cupón {coupon.code} para otro pedido."
            )
        return f"Tu pedido ya tiene envío gratis, así que el cupón {coupon.code} no se aplica."
    return None


def coupon_rejection_reason(coupon, subtotal: Decimal) -> Optional[str]:
    """Texto para rechazar el cupón al aplicarlo si ahora no tendría efecto."""
    return _inapplicable(coupon, Decimal(subtotal), for_apply=True)


def compute_totals(subtotal: Decimal, coupon=None) -> Totals:
    subtotal = Decimal(subtotal)
    discount = Decimal("0")
    shipping_discount = Decimal("0")
    is_shipping_coupon = coupon is not None and coupon.discount_type == FREE_SHIPPING_TYPE
    coupon_applies = False
    coupon_message = None

    if coupon is not None and subtotal > 0:
        coupon_message = _inapplicable(coupon, subtotal, for_apply=False)
        if coupon_message is None:
            coupon_applies = True
            if not is_shipping_coupon:
                discount = coupon.calculate_discount(subtotal)

    after_discount = subtotal - discount
    shipping_base = _shipping_base(after_discount)
    if coupon_applies and is_shipping_coupon:
        shipping_discount = shipping_base
    shipping_cost = shipping_base - shipping_discount

    if shipping_discount > 0:
        message = f"Envío gratis con el cupón {coupon.code}"
    elif shipping_cost == 0:
        message = "Envío gratis"
    else:
        message = f"Te faltan {format_eur(_threshold() - after_discount)} para el envío gratis"

    return Totals(
        subtotal=subtotal,
        discount=discount,
        shipping_base=shipping_base,
        shipping_discount=shipping_discount,
        shipping_cost=shipping_cost,
        total=after_discount + shipping_cost,
        shipping_message=message,
        coupon_applies=coupon_applies,
        coupon_message=coupon_message,
    )
