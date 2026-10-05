"""
Invoice PDF rendering.

Renderiza el PDF de una factura ya emitida leyendo ÚNICAMENTE sus copias
(seller_snapshot / buyer_snapshot / lines_snapshot), nunca el pedido ni el
usuario actuales: así el documento es el mismo aunque el cliente cambie sus
datos o borre su cuenta. La emisión y numeración viven en services/invoicing.py.
"""
import io
from datetime import timezone
from decimal import Decimal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

MADRID = ZoneInfo("Europe/Madrid")

GRANATE = colors.Color(0x7B / 255, 0x17 / 255, 0x16 / 255)
LIGHT_BG = colors.Color(0xF4 / 255, 0xF1 / 255, 0xE9 / 255)
CARD_BG = colors.Color(0xED / 255, 0xE9 / 255, 0xDF / 255)
INK = colors.Color(0x1C / 255, 0x1A / 255, 0x14 / 255)
MUTED = colors.Color(0x6B / 255, 0x64 / 255, 0x56 / 255)

_TITLES = {
    "simplified": "FACTURA SIMPLIFICADA",
    "full": "FACTURA",
    "corrective": "FACTURA RECTIFICATIVA",
}


def local_date(dt) -> str:
    """Fecha de emisión en hora peninsular (issued_at se guarda en UTC naive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MADRID).strftime("%d/%m/%Y")


def _eur(value) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "−" if amount < 0 else ""
    return f"{sign}{abs(amount):.2f} €".replace(".", ",")


def _pct(rate) -> str:
    pct = (Decimal(str(rate)) * 100).normalize()
    return f"{pct:f}".rstrip("0").rstrip(".") + " %"


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text or "")), style)


def render_invoice_pdf(invoice) -> bytes:
    """Genera el PDF de *invoice* (models.Invoice). Salida determinista."""
    seller = invoice.seller_snapshot or {}
    buyer = invoice.buyer_snapshot or {}
    lines = invoice.lines_snapshot or {}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Factura {invoice.invoice_number}",
        author=seller.get("name", ""),
        invariant=1,  # sin fecha de creación ni id aleatorio → mismo hash al regenerar
    )

    h1 = ParagraphStyle("h1", fontSize=22, textColor=GRANATE, fontName="Helvetica-Bold", spaceAfter=2, leading=26)
    h2 = ParagraphStyle("h2", fontSize=11, textColor=GRANATE, fontName="Helvetica-Bold", spaceAfter=4)
    normal = ParagraphStyle("normal", fontSize=9, textColor=INK, fontName="Helvetica", leading=13)
    muted = ParagraphStyle("muted", fontSize=8, textColor=MUTED, fontName="Helvetica", leading=12)
    right_normal = ParagraphStyle("right_normal", fontSize=9, textColor=MUTED, fontName="Helvetica", alignment=TA_RIGHT)
    center_small = ParagraphStyle("center_small", fontSize=8, textColor=MUTED, fontName="Helvetica", alignment=TA_CENTER)
    th = ParagraphStyle("th", fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")
    th_c = ParagraphStyle("th_c", parent=th, alignment=TA_CENTER)
    th_r = ParagraphStyle("th_r", parent=th, alignment=TA_RIGHT)
    td_c = ParagraphStyle("td_c", fontSize=9, textColor=INK, fontName="Helvetica", alignment=TA_CENTER)
    td_r = ParagraphStyle("td_r", fontSize=9, textColor=INK, fontName="Helvetica", alignment=TA_RIGHT)

    story = []

    # ── Cabecera ────────────────────────────────────────────────────────────
    title = _TITLES.get(invoice.invoice_type, "FACTURA")
    header = Table(
        [
            [Paragraph("CREMACUADRADO", h1),
             Paragraph(title, ParagraphStyle("inv", fontSize=16, textColor=GRANATE,
                                             fontName="Helvetica-Bold", alignment=TA_RIGHT, leading=20))],
            [Paragraph("crema de pistacho manchego artesanal", muted),
             _p(f"Nº {invoice.invoice_number}", ParagraphStyle("invn", fontSize=10, textColor=INK,
                                                               fontName="Helvetica-Bold", alignment=TA_RIGHT))],
            [Paragraph("", normal), _p(f"Fecha de expedición: {local_date(invoice.issued_at)}", right_normal)],
        ],
        colWidths=[100 * mm, 70 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(header)
    story.append(HRFlowable(width="100%", thickness=2, color=GRANATE, spaceAfter=8))

    # ── Emisor y destinatario ───────────────────────────────────────────────
    seller_lines = [
        seller.get("name"),
        f"NIF: {seller.get('nif', '')}",
        seller.get("address"),
        f"{seller.get('postal_code', '')} {seller.get('city', '')}",
        f"{seller.get('province', '')}, {seller.get('country', '')}",
        seller.get("email"),
        seller.get("phone"),
    ]
    addr = buyer.get("address") or {}
    buyer_lines = [
        buyer.get("name"),
        f"NIF: {buyer['nif']}" if buyer.get("nif") else "",
        addr.get("street"),
        addr.get("street_2"),
        f"{addr.get('postal_code', '')} {addr.get('city', '')}",
        ", ".join(x for x in (addr.get("province"), addr.get("country")) if x),
        buyer.get("email"),
    ]

    def block(values):
        return Paragraph("<br/>".join(escape(str(v)) for v in values if v and str(v).strip()), normal)

    parties = Table(
        [[Paragraph("EMISOR", h2), Paragraph("", normal), Paragraph("DESTINATARIO", h2)],
         [block(seller_lines), Paragraph("", normal), block(buyer_lines)]],
        colWidths=[80 * mm, 10 * mm, 80 * mm],
    )
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (0, 1), CARD_BG),
        ("BACKGROUND", (2, 1), (2, 1), CARD_BG),
        ("TOPPADDING", (0, 1), (-1, 1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 1), (0, 1), 8),
        ("LEFTPADDING", (2, 1), (2, 1), 8),
        ("RIGHTPADDING", (2, 1), (2, 1), 8),
    ]))
    story.append(parties)
    story.append(Spacer(1, 6 * mm))

    # ── Referencias ─────────────────────────────────────────────────────────
    ref_cells = [_p(f"Pedido: {lines.get('order_number', '')}", normal),
                 _p(f"Forma de pago: {lines.get('payment_method') or 'Tarjeta'}", normal)]
    ref_rows = [ref_cells]
    if invoice.invoice_type == "corrective":
        ref_rows.append([
            _p(f"Rectifica a la factura {lines.get('rectified_invoice_number', '')} "
               f"de {lines.get('rectified_issued_on', '')}", normal),
            _p(f"Motivo: {lines.get('reason') or 'Devolución'}", normal),
        ])
    refs = Table(ref_rows, colWidths=[85 * mm, 85 * mm])
    refs.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, MUTED),
    ]))
    story.append(refs)
    story.append(Spacer(1, 5 * mm))

    # ── Líneas (importes con IVA incluido) ──────────────────────────────────
    rows = [[Paragraph("DESCRIPCIÓN", th), Paragraph("UDS", th_c),
             Paragraph("PRECIO UNIT.", th_r), Paragraph("IMPORTE", th_r)]]
    for item in lines.get("items", []):
        rows.append([
            _p(item.get("description"), normal),
            _p(item.get("quantity"), td_c),
            _p(_eur(item.get("unit_price", 0)), td_r),
            _p(_eur(item.get("total", 0)), td_r),
        ])
    items_table = Table(rows, colWidths=[100 * mm, 15 * mm, 30 * mm, 25 * mm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GRANATE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 8),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MUTED),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 5 * mm))

    # ── Totales ─────────────────────────────────────────────────────────────
    totals = [("Subtotal productos", lines.get("subtotal", "0"))]
    if Decimal(str(lines.get("shipping", "0"))) != 0:
        totals.append(("Gastos de envío", lines["shipping"]))
    if Decimal(str(lines.get("discount", "0"))) != 0:
        label = f"Descuento ({lines['coupon_code']})" if lines.get("coupon_code") else "Descuento"
        totals.append((label, -Decimal(str(lines["discount"]))))
    totals.append(("Base imponible", invoice.tax_base))
    totals.append((f"IVA ({_pct(invoice.tax_rate)})", invoice.tax_amount))

    total_style_l = ParagraphStyle("tb", fontSize=12, textColor=GRANATE, fontName="Helvetica-Bold")
    total_style_r = ParagraphStyle("tr", parent=total_style_l, alignment=TA_RIGHT)
    totals_inner = Table(
        [[_p(label, muted), _p(_eur(value), right_normal)] for label, value in totals]
        + [[Paragraph("TOTAL", total_style_l), _p(_eur(invoice.total), total_style_r)]],
        colWidths=[50 * mm, 30 * mm],
        style=TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LINEABOVE", (0, -1), (-1, -1), 1.5, GRANATE),
            ("TOPPADDING", (0, -1), (-1, -1), 6),
        ]),
    )
    story.append(Table([[Paragraph("", normal), totals_inner]], colWidths=[90 * mm, 80 * mm]))
    story.append(Spacer(1, 10 * mm))

    # ── Pie ─────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=MUTED, spaceAfter=4))
    story.append(Paragraph(
        "Precios con IVA incluido. Documento expedido electrónicamente, válido sin firma.",
        center_small,
    ))
    story.append(Spacer(1, 2))
    story.append(_p(
        f"{seller.get('name', '')} · NIF {seller.get('nif', '')} · {seller.get('address', '')}, "
        f"{seller.get('postal_code', '')} {seller.get('city', '')}"
        + (f" · {seller['registry']}" if seller.get("registry") else ""),
        center_small,
    ))

    doc.build(story)
    return buf.getvalue()
