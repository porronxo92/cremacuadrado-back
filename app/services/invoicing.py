"""
Emisión de facturas.

- Numeración correlativa sin huecos por serie y año: F2026-000001 (ordinarias y
  simplificadas) y R2026-000001 (rectificativas). El número se asigna con un
  UPDATE ... RETURNING sobre invoice_sequences en la misma transacción que
  inserta la factura, así que dos pagos simultáneos nunca reciben el mismo número.
- La factura guarda copias de emisor, destinatario y líneas: es la fuente de
  verdad para regenerar el PDF y no depende del estado actual del pedido/usuario.
- Cada factura encadena el hash de la anterior de su serie (preparación VeriFactu).
- El PDF se guarda una sola vez en el Blob privado; los reenvíos usan ese mismo PDF.

Las funciones no hacen commit: lo hace quien las llama.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.invoice import Invoice
from app.models.order import Order
from app.models.payment import Refund
from app.services import private_blob
from app.services.company import seller_snapshot
from app.services.invoice import MADRID, local_date, render_invoice_pdf

logger = logging.getLogger("cremacuadrado.invoicing")

SERIES_ORDINARY = "F"
SERIES_CORRECTIVE = "R"
CENT = Decimal("0.01")

INVOICEABLE_STATUSES = {"paid", "processing", "shipped", "delivered", "partially_refunded", "refunded"}

_REFUND_REASONS = {
    "requested_by_customer": "Devolución solicitada por el cliente",
    "duplicate": "Cargo duplicado",
    "fraudulent": "Cargo fraudulento",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(MADRID)


def _money(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _s(value) -> str:
    """Importe como texto en los snapshots JSON (evita floats)."""
    return str(_money(value))


def split_tax(total: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Desglosa un importe con IVA incluido en (base imponible, cuota)."""
    total = _money(total)
    rate = Decimal(str(rate))
    tax = (total * rate / (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    return total - tax, tax


def format_number(series: str, year: int, number: int) -> str:
    return f"{series}{year}-{number:06d}"


def allocate_number(db: Session, series: str, year: int) -> int:
    """Siguiente número de la serie. Bloquea la fila hasta el fin de la transacción."""
    db.execute(
        text(
            "INSERT INTO invoice_sequences (series, year, last_number) VALUES (:s, :y, 0) "
            "ON CONFLICT (series, year) DO NOTHING"
        ),
        {"s": series, "y": year},
    )
    return db.execute(
        text(
            "UPDATE invoice_sequences SET last_number = last_number + 1 "
            "WHERE series = :s AND year = :y RETURNING last_number"
        ),
        {"s": series, "y": year},
    ).scalar_one()


def _previous_hash(db: Session, series: str) -> Optional[str]:
    last = (
        db.query(Invoice.record_hash)
        .filter(Invoice.series == series)
        .order_by(Invoice.year.desc(), Invoice.number.desc())
        .first()
    )
    return last[0] if last else None


def compute_record_hash(invoice: Invoice) -> str:
    canonical = {
        "previous_hash": invoice.previous_hash or "",
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type,
        "issued_at": invoice.issued_at.isoformat(),
        "seller_nif": (invoice.seller_snapshot or {}).get("nif", ""),
        "buyer_nif": (invoice.buyer_snapshot or {}).get("nif") or "",
        "tax_base": _s(invoice.tax_base),
        "tax_rate": str(Decimal(str(invoice.tax_rate)).normalize()),
        "tax_amount": _s(invoice.tax_amount),
        "total": _s(invoice.total),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pdf_pathname(invoice: Invoice) -> str:
    """invoices/AAAA/MM/<número>.pdf — sin datos personales en la ruta."""
    local = _local(invoice.issued_at)
    return f"invoices/{local:%Y}/{local:%m}/{invoice.invoice_number}.pdf"


def month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """Inicio y fin (exclusivo) de un mes natural peninsular, en UTC naive."""
    start = datetime(year, month, 1, tzinfo=MADRID)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=MADRID)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def buyer_from_order(order: Order) -> dict:
    shipping = order.shipping_address or {}
    billing = order.billing_address or {}
    if billing.get("nif"):
        name = billing.get("name") or f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        source = billing
        nif = billing["nif"].upper()
    else:
        name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
        source = shipping
        nif = None
    return {
        "name": name,
        "nif": nif,
        "email": order.customer_email,
        "phone": shipping.get("phone"),
        "address": {
            "street": source.get("street"),
            "street_2": source.get("street_2"),
            "postal_code": source.get("postal_code"),
            "city": source.get("city"),
            "province": source.get("province"),
            "country": source.get("country") or "España",
        },
    }


def lines_from_order(order: Order) -> dict:
    items = []
    for item in order.items:
        description = item.product_name + (f" ({item.product_sku})" if item.product_sku else "")
        items.append({
            "description": description,
            "sku": item.product_sku,
            "quantity": item.quantity,
            "unit_price": _s(item.unit_price),
            "total": _s(item.total),
        })
    return {
        "order_number": order.order_number,
        "payment_method": "Tarjeta" if (order.payment_method or "card") == "card" else order.payment_method,
        "items": items,
        "subtotal": _s(order.subtotal),
        "shipping": _s(order.shipping_cost),
        "discount": _s(order.discount),
        "coupon_code": order.coupon_code,
    }


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------

def primary_invoice(db: Session, order_id: int) -> Optional[Invoice]:
    return (
        db.query(Invoice)
        .filter(Invoice.order_id == order_id, Invoice.invoice_type != Invoice.TYPE_CORRECTIVE)
        .first()
    )


def _create(db: Session, *, series: str, invoice_type: str, order: Order, issued_at: datetime,
            buyer: dict, lines: dict, total: Decimal, tax_rate: Decimal,
            rectifies: Optional[Invoice] = None, refund: Optional[Refund] = None) -> Invoice:
    year = _local(issued_at).year
    number = allocate_number(db, series, year)
    base, tax = split_tax(total, tax_rate)
    invoice = Invoice(
        invoice_number=format_number(series, year, number),
        series=series,
        year=year,
        number=number,
        invoice_type=invoice_type,
        order_id=order.id,
        user_id=order.user_id,
        rectifies_invoice_id=rectifies.id if rectifies else None,
        refund_id=refund.id if refund else None,
        issued_at=issued_at,
        seller_snapshot=seller_snapshot(),
        buyer_snapshot=buyer,
        lines_snapshot=lines,
        tax_base=base,
        tax_rate=tax_rate,
        tax_amount=tax,
        total=_money(total),
        currency="EUR",
        previous_hash=_previous_hash(db, series),
        pdf_status="pending",
        sent_count=0,
        created_at=_utcnow(),
    )
    invoice.record_hash = compute_record_hash(invoice)
    db.add(invoice)
    db.flush()
    logger.info("Invoice issued: number=%s order=%s total=%s", invoice.invoice_number, order.order_number, invoice.total)
    return invoice


def issue_invoice_for_order(db: Session, order: Order, issued_at: Optional[datetime] = None) -> Invoice:
    """Emite la factura del pedido. Idempotente: si ya existe, la devuelve."""
    # Bloquea el pedido para que webhook y confirmación no emitan dos veces
    db.query(Order).filter(Order.id == order.id).with_for_update().one()

    existing = primary_invoice(db, order.id)
    if existing:
        return existing

    if order.status not in INVOICEABLE_STATUSES:
        raise ValueError(f"El pedido {order.order_number} no está pagado (estado {order.status})")

    if order.tax_rate is None:
        order.tax_rate = Decimal(str(settings.TAX_RATE))
    buyer = buyer_from_order(order)
    return _create(
        db,
        series=SERIES_ORDINARY,
        invoice_type=Invoice.TYPE_FULL if buyer.get("nif") else Invoice.TYPE_SIMPLIFIED,
        order=order,
        issued_at=_to_naive_utc(issued_at or order.paid_at),
        buyer=buyer,
        lines=lines_from_order(order),
        total=order.total,
        tax_rate=Decimal(str(order.tax_rate)),
    )


def issue_corrective_for_refund(db: Session, order: Order, refund: Refund,
                                issued_at: Optional[datetime] = None) -> Optional[Invoice]:
    """Factura rectificativa por un reembolso de Stripe ya completado. Idempotente."""
    if refund.status != "succeeded":
        return None
    existing = db.query(Invoice).filter(Invoice.refund_id == refund.id).first()
    if existing:
        return existing

    original = issue_invoice_for_order(db, order)
    amount = (Decimal(refund.amount) / 100).quantize(CENT)
    reason = _REFUND_REASONS.get(refund.reason or "", "Devolución")
    base_lines = original.lines_snapshot or {}

    if amount >= _money(original.total):
        lines = {
            **base_lines,
            "items": [
                {**item, "quantity": item["quantity"],
                 "unit_price": _s(-Decimal(item["unit_price"])), "total": _s(-Decimal(item["total"]))}
                for item in base_lines.get("items", [])
            ],
            "subtotal": _s(-Decimal(base_lines.get("subtotal", "0"))),
            "shipping": _s(-Decimal(base_lines.get("shipping", "0"))),
            "discount": _s(-Decimal(base_lines.get("discount", "0"))),
        }
        total = -_money(original.total)
    else:
        lines = {
            "order_number": base_lines.get("order_number"),
            "payment_method": base_lines.get("payment_method"),
            "items": [{
                "description": f"Devolución parcial del pedido {base_lines.get('order_number', '')}",
                "sku": None,
                "quantity": 1,
                "unit_price": _s(-amount),
                "total": _s(-amount),
            }],
            "subtotal": _s(-amount),
            "shipping": "0.00",
            "discount": "0.00",
            "coupon_code": None,
        }
        total = -amount

    lines.update({
        "rectified_invoice_number": original.invoice_number,
        "rectified_issued_on": local_date(original.issued_at),
        "reason": reason,
        "stripe_refund_id": refund.stripe_refund_id,
    })
    return _create(
        db,
        series=SERIES_CORRECTIVE,
        invoice_type=Invoice.TYPE_CORRECTIVE,
        order=order,
        issued_at=_to_naive_utc(issued_at),
        buyer=original.buyer_snapshot,
        lines=lines,
        total=total,
        tax_rate=Decimal(str(original.tax_rate)),
        rectifies=original,
        refund=refund,
    )


# ---------------------------------------------------------------------------
# PDF en Vercel Blob
# ---------------------------------------------------------------------------

def store_pdf(db: Session, invoice: Invoice) -> bytes:
    """Genera el PDF y lo guarda en el Blob. Devuelve los bytes aunque falle la subida."""
    pdf = render_invoice_pdf(invoice)
    if not private_blob.is_configured():
        logger.warning("Invoice PDF not stored (blob not configured): number=%s", invoice.invoice_number)
        return pdf
    pathname = pdf_pathname(invoice)
    try:
        private_blob.upload(pdf, pathname)
    except Exception as exc:
        invoice.pdf_status = "failed"
        logger.error("Invoice PDF upload failed: number=%s error=%s", invoice.invoice_number, exc, exc_info=True)
        return pdf
    invoice.blob_pathname = pathname
    invoice.pdf_sha256 = hashlib.sha256(pdf).hexdigest()
    invoice.pdf_size = len(pdf)
    invoice.pdf_status = "stored"
    return pdf


def get_pdf(db: Session, invoice: Invoice) -> bytes:
    """PDF guardado de la factura. Si falta, lo regenera desde la copia y lo guarda."""
    if invoice.pdf_status == "stored" and invoice.blob_pathname:
        try:
            pdf = private_blob.download(invoice.blob_pathname)
        except private_blob.BlobNotFound:
            logger.error("Invoice PDF missing in blob: number=%s path=%s", invoice.invoice_number, invoice.blob_pathname)
        else:
            if hashlib.sha256(pdf).hexdigest() == invoice.pdf_sha256:
                return pdf
            logger.error("Invoice PDF hash mismatch: number=%s", invoice.invoice_number)
    return store_pdf(db, invoice)


def mark_sent(invoice: Invoice) -> None:
    invoice.sent_count = (invoice.sent_count or 0) + 1
    invoice.last_sent_at = _utcnow()
