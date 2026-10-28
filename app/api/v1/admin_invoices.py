"""
Admin API — facturas emitidas: listado por mes, descarga individual,
regeneración del PDF y exportación mensual (ZIP con los PDF + libro de
facturas emitidas en CSV) para el cierre del mes.
"""
import csv
import io
import logging
import re
import zipfile
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Query as SAQuery, joinedload

from app.api.deps import AdminUser, DbSession
from app.models.invoice import Invoice
from app.models.order import Order
from app.schemas.common import Message, PaginatedResponse
from app.schemas.invoice import AdminInvoiceItem, AdminInvoiceTotals
from app.services import invoicing
from app.services.invoice import local_date

logger = logging.getLogger("cremacuadrado.admin.invoices")

router = APIRouter()

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def _parse_month(month: str) -> tuple[int, int]:
    match = _MONTH_RE.match(month or "")
    if not match:
        raise HTTPException(status_code=400, detail="Mes no válido, usa el formato AAAA-MM")
    return int(match.group(1)), int(match.group(2))


def _filtered(db, month: Optional[str], invoice_type: Optional[str], search: Optional[str]) -> SAQuery:
    q = db.query(Invoice)
    if month:
        start, end = invoicing.month_bounds_utc(*_parse_month(month))
        q = q.filter(Invoice.issued_at >= start, Invoice.issued_at < end)
    if invoice_type:
        q = q.filter(Invoice.invoice_type == invoice_type)
    if search:
        like = f"%{search.strip()}%"
        q = q.join(Order, Order.id == Invoice.order_id).filter(or_(
            Invoice.invoice_number.ilike(like),
            Order.order_number.ilike(like),
            Order.guest_email.ilike(like),
            Invoice.buyer_snapshot["name"].as_string().ilike(like),
            Invoice.buyer_snapshot["email"].as_string().ilike(like),
            Invoice.buyer_snapshot["nif"].as_string().ilike(like),
        ))
    return q


def _to_item(inv: Invoice) -> AdminInvoiceItem:
    buyer = inv.buyer_snapshot or {}
    return AdminInvoiceItem(
        id=inv.id,
        invoice_number=inv.invoice_number,
        invoice_type=inv.invoice_type,
        issued_at=inv.issued_at,
        order_id=inv.order_id,
        order_number=(inv.lines_snapshot or {}).get("order_number"),
        user_id=inv.user_id,
        buyer_name=buyer.get("name"),
        buyer_nif=buyer.get("nif"),
        buyer_email=buyer.get("email"),
        tax_base=inv.tax_base,
        tax_rate=inv.tax_rate,
        tax_amount=inv.tax_amount,
        total=inv.total,
        rectifies_number=inv.rectifies.invoice_number if inv.rectifies else None,
        pdf_status=inv.pdf_status,
        sent_count=inv.sent_count,
        last_sent_at=inv.last_sent_at,
    )


def _get(db, invoice_id: int) -> Invoice:
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return invoice


@router.get("/invoices", response_model=PaginatedResponse[AdminInvoiceItem])
def list_invoices(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    month: Optional[str] = Query(None, description="AAAA-MM (hora peninsular)"),
    invoice_type: Optional[str] = Query(None, alias="type"),
    search: Optional[str] = None,
):
    q = _filtered(db, month, invoice_type, search)
    total = q.order_by(None).count()
    rows = (
        q.options(joinedload(Invoice.rectifies))
        .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedResponse.create([_to_item(r) for r in rows], total, page, page_size)


@router.get("/invoices/totals", response_model=AdminInvoiceTotals)
def invoice_totals(
    db: DbSession,
    admin_user: AdminUser,
    month: Optional[str] = None,
    invoice_type: Optional[str] = Query(None, alias="type"),
    search: Optional[str] = None,
):
    q = _filtered(db, month, invoice_type, search)
    count, base, tax, total = q.with_entities(
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.tax_base), 0),
        func.coalesce(func.sum(Invoice.tax_amount), 0),
        func.coalesce(func.sum(Invoice.total), 0),
    ).one()
    return AdminInvoiceTotals(count=count, tax_base=Decimal(base), tax_amount=Decimal(tax), total=Decimal(total))


@router.get("/invoices/export")
def export_month(
    db: DbSession,
    admin_user: AdminUser,
    month: str = Query(..., description="AAAA-MM"),
):
    """ZIP con los PDF del mes y el libro de facturas emitidas (CSV).

    Ojo: la respuesta de una función de Vercel está limitada (~4,5 MB). Con PDF
    de unos pocos KB alcanza para cientos de facturas al mes.
    """
    year, mon = _parse_month(month)
    invoices = (
        _filtered(db, month, None, None)
        .options(joinedload(Invoice.rectifies))
        .order_by(Invoice.series, Invoice.number)
        .all()
    )
    if not invoices:
        raise HTTPException(status_code=404, detail="No hay facturas en ese mes")

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf, delimiter=";")
    writer.writerow([
        "Número", "Tipo", "Fecha expedición", "Pedido", "Destinatario", "NIF destinatario",
        "Email", "Base imponible", "Tipo IVA", "Cuota IVA", "Total", "Rectifica a", "Huella (SHA-256)",
    ])

    def num(value) -> str:
        return f"{Decimal(value):.2f}".replace(".", ",")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for inv in invoices:
            buyer = inv.buyer_snapshot or {}
            writer.writerow([
                inv.invoice_number,
                {"simplified": "Simplificada", "full": "Completa", "corrective": "Rectificativa"}.get(inv.invoice_type, inv.invoice_type),
                local_date(inv.issued_at),
                (inv.lines_snapshot or {}).get("order_number", ""),
                buyer.get("name", ""),
                buyer.get("nif") or "",
                buyer.get("email") or "",
                num(inv.tax_base),
                f"{Decimal(inv.tax_rate) * 100:.2f}".replace(".", ",") + " %",
                num(inv.tax_amount),
                num(inv.total),
                inv.rectifies.invoice_number if inv.rectifies else "",
                inv.record_hash,
            ])
            zf.writestr(f"facturas/{inv.pdf_filename}", invoicing.get_pdf(db, inv))
        # BOM: Excel abre bien las tildes
        zf.writestr(f"libro_facturas_emitidas_{year}-{mon:02d}.csv", "\ufeff" + csv_buf.getvalue())

    db.commit()  # PDFs regenerated/stored on the way
    logger.info("Invoice month export: month=%s count=%s admin=%s", month, len(invoices), admin_user.id)
    zip_buf.seek(0)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="facturas_{year}-{mon:02d}.zip"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/invoices/{invoice_id}/pdf")
def download_invoice_pdf(invoice_id: int, db: DbSession, admin_user: AdminUser):
    invoice = _get(db, invoice_id)
    pdf_bytes = invoicing.get_pdf(db, invoice)
    db.commit()
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.pdf_filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/invoices/{invoice_id}/regenerate-pdf", response_model=Message)
def regenerate_invoice_pdf(invoice_id: int, db: DbSession, admin_user: AdminUser):
    """Vuelve a generar y subir el PDF desde la copia guardada. El número no cambia."""
    invoice = _get(db, invoice_id)
    invoicing.store_pdf(db, invoice)
    db.commit()
    if invoice.pdf_status != "stored":
        raise HTTPException(status_code=502, detail="No se ha podido guardar el PDF en el almacenamiento")
    return Message(message=f"PDF de la factura {invoice.invoice_number} regenerado")


@router.post("/orders/{order_id}/invoice", response_model=AdminInvoiceItem)
def issue_order_invoice(order_id: int, db: DbSession, admin_user: AdminUser):
    """Emite la factura de un pedido pagado que no la tenga (p. ej. si falló al pagar)."""
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if order.status not in invoicing.INVOICEABLE_STATUSES:
        raise HTTPException(status_code=400, detail="El pedido no está pagado")
    invoice = invoicing.issue_invoice_for_order(db, order)
    db.commit()
    invoicing.store_pdf(db, invoice)
    db.commit()
    return _to_item(invoice)
