"""
Invoice PDF rendering.

Renderiza el PDF de una factura ya emitida leyendo ÚNICAMENTE sus copias
(seller_snapshot / buyer_snapshot / lines_snapshot), nunca el pedido ni el
usuario actuales: así el documento es el mismo aunque el cliente cambie sus
datos o borre su cuenta. La emisión y numeración viven en services/invoicing.py.

Las facturas ya guardadas en el Blob conservan su PDF original (get_pdf lo
verifica por hash); un cambio de diseño solo afecta a las nuevas o a las que
haya que regenerar.
"""
import io
from datetime import timezone
from decimal import Decimal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.graphics.shapes import Drawing, Polygon
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MADRID = ZoneInfo("Europe/Madrid")

GRANATE = colors.Color(0x7B / 255, 0x17 / 255, 0x16 / 255)
AMARILLO = colors.Color(0xE6 / 255, 0xC1 / 255, 0x5A / 255)
LIGHT_BG = colors.Color(0xF4 / 255, 0xF1 / 255, 0xE9 / 255)
CARD_BG = colors.Color(0xED / 255, 0xE9 / 255, 0xDF / 255)
INK = colors.Color(0x1C / 255, 0x1A / 255, 0x14 / 255)
MUTED = colors.Color(0x6B / 255, 0x64 / 255, 0x56 / 255)
RULE = colors.Color(0xE8 / 255, 0xE3 / 255, 0xD8 / 255)

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_TITLES = {
    "simplified": "FACTURA SIMPLIFICADA",
    "full": "FACTURA",
    "corrective": "FACTURA RECTIFICATIVA",
}

# ── Logo ────────────────────────────────────────────────────────────────────
# Copia vectorial de frontend/src/assets/images/logo.svg (viewBox 109.15×116.86),
# que solo contiene polígonos: se dibuja con reportlab sin dependencias de SVG.
_LOGO_W, _LOGO_H = 109.15, 116.86
_LOGO_SHAPES = [
    # [  (corchete izquierdo)
    [7.71, 46.26, 21.82, 46.26, 21.82, 38.55, 0, 38.55, 0, 116.86, 21.82, 116.86,
     21.82, 109.15, 7.71, 109.15],
    # ]  (corchete derecho)
    [56.48, 46.26, 70.6, 46.26, 70.6, 109.15, 56.48, 109.15, 56.48, 116.86, 78.3, 116.86,
     78.3, 38.55, 56.48, 38.55],
    # ²  (trazo superior y base)
    [86.02, 0, 109.15, 0, 109.15, 15.42, 101.44, 15.42, 101.44, 7.71, 86.02, 7.71],
    [86.02, 15.42, 93.73, 15.42, 93.73, 23.13, 109.15, 23.13, 109.15, 30.84, 86.02, 30.84],
    # A  (path del SVG con su translate(-45.42 -41.57) ya aplicado)
    [42.99, 57.43, 35.39, 57.43, 19.58, 98.03, 27.70, 98.03, 31.34, 88.55, 46.97, 88.55,
     50.58, 98.03, 58.76, 98.03],
]
_LOGO_A_COUNTER = [34.24, 80.87, 39.11, 67.73, 44.01, 80.87]  # hueco de la «A»


def _logo(height: float, color=GRANATE, background=colors.white) -> Drawing:
    """Logo de CremaCuadrado de *height* puntos de alto (eje Y invertido respecto al SVG)."""
    scale = height / _LOGO_H
    drawing = Drawing(_LOGO_W * scale, height)

    def points(coords):
        out = []
        for i in range(0, len(coords), 2):
            out += [coords[i] * scale, (_LOGO_H - coords[i + 1]) * scale]
        return out

    for shape in _LOGO_SHAPES:
        drawing.add(Polygon(points(shape), fillColor=color, strokeColor=None, strokeWidth=0))
    drawing.add(Polygon(points(_LOGO_A_COUNTER), fillColor=background, strokeColor=None, strokeWidth=0))
    return drawing


# ── Utilidades ──────────────────────────────────────────────────────────────

def local_date(dt) -> str:
    """Fecha de emisión en hora peninsular (issued_at se guarda en UTC naive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MADRID).strftime("%d/%m/%Y")


def _eur(value) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "-" if amount < 0 else ""  # guion ASCII: Helvetica estándar no tiene «−»
    return f"{sign}{abs(amount):.2f} €".replace(".", ",")


def _pct(rate) -> str:
    """0.10 → "10 %", 0.21 → "21 %", 0.055 → "5,5 %"."""
    pct = (Decimal(str(rate)) * 100).quantize(Decimal("0.01"))
    text = f"{pct:f}"
    if "." in text:  # solo se recortan ceros decimales, nunca los de la parte entera
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",") + " %"


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text or "")), style)


# ── Render ──────────────────────────────────────────────────────────────────

def render_invoice_pdf(invoice) -> bytes:
    """Genera el PDF de *invoice* (models.Invoice). Salida determinista."""
    seller = invoice.seller_snapshot or {}
    buyer = invoice.buyer_snapshot or {}
    lines = invoice.lines_snapshot or {}
    title = _TITLES.get(invoice.invoice_type, "FACTURA")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=14 * mm,
        bottomMargin=26 * mm,  # deja sitio al pie que se dibuja en _footer
        title=f"Factura {invoice.invoice_number}",
        author=seller.get("name", ""),
        invariant=1,  # sin fecha de creación ni id aleatorio → mismo hash al regenerar
    )

    def style(name, **kw) -> ParagraphStyle:
        if "parent" in kw:  # hereda todo del padre (sin pisarlo con los valores base)
            return ParagraphStyle(name, **kw)
        base = {"fontName": "Helvetica", "fontSize": 9, "leading": 13, "textColor": INK}
        base.update(kw)
        return ParagraphStyle(name, **base)

    brand = style("brand", fontName="Helvetica-Bold", fontSize=17, leading=19, textColor=GRANATE)
    tagline = style("tagline", fontName="Helvetica-Oblique", fontSize=8.5, leading=11, textColor=MUTED)
    doc_title = style("doc_title", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=GRANATE,
                      alignment=TA_RIGHT)
    label = style("label", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=MUTED)
    value = style("value", fontName="Helvetica-Bold", fontSize=10, leading=13)
    value_r = style("value_r", parent=value, alignment=TA_RIGHT)
    normal = style("normal")
    muted = style("muted", fontSize=8.5, leading=12, textColor=MUTED)
    th = style("th", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white)
    th_c = style("th_c", parent=th, alignment=TA_CENTER)
    th_r = style("th_r", parent=th, alignment=TA_RIGHT)
    td = style("td", fontSize=9.5)
    td_c = style("td_c", parent=td, alignment=TA_CENTER)
    td_r = style("td_r", parent=td, alignment=TA_RIGHT)
    tot_l = style("tot_l", textColor=MUTED)
    tot_r = style("tot_r", alignment=TA_RIGHT)
    grand_l = style("grand_l", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.white)
    grand_r = style("grand_r", parent=grand_l, alignment=TA_RIGHT)

    story = []

    # ── Cabecera: logo + marca | tipo de documento, número y fecha ──────────
    logo_h = 17 * mm
    brand_block = Table(
        [[_logo(logo_h), [Paragraph("CREMACUADRADO", brand),
                          Paragraph("Crema de pistacho manchego artesanal", tagline)]]],
        colWidths=[logo_h * _LOGO_W / _LOGO_H + 4 * mm, None],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )
    meta_label = style("meta_label", fontSize=8.5, leading=12, textColor=MUTED, alignment=TA_RIGHT)
    doc_block = [
        Paragraph(title, doc_title),
        Spacer(1, 2.5 * mm),
        Table(
            [[Paragraph("Número", meta_label), _p(invoice.invoice_number, value_r)],
             [Paragraph("Fecha de expedición", meta_label), _p(local_date(invoice.issued_at), value_r)]],
            colWidths=[40 * mm, 35 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 3 * mm),
                ("RIGHTPADDING", (1, 0), (1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]),
            hAlign="RIGHT",
        ),
    ]
    header = Table([[brand_block, doc_block]], colWidths=[CONTENT_W - 75 * mm, 75 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 * mm),
        ("LINEBELOW", (0, 0), (-1, 0), 2, GRANATE),
    ]))
    story.append(header)
    story.append(Spacer(1, 7 * mm))

    # ── Emisor y destinatario ───────────────────────────────────────────────
    seller_lines = [
        f"NIF: {seller.get('nif', '')}",
        seller.get("address"),
        f"{seller.get('postal_code', '')} {seller.get('city', '')}",
        ", ".join(x for x in (seller.get("province"), seller.get("country")) if x),
        seller.get("email"),
        seller.get("phone"),
    ]
    addr = buyer.get("address") or {}
    buyer_lines = [
        f"NIF: {buyer['nif']}" if buyer.get("nif") else "",
        addr.get("street"),
        addr.get("street_2"),
        f"{addr.get('postal_code', '')} {addr.get('city', '')}",
        ", ".join(x for x in (addr.get("province"), addr.get("country")) if x),
        buyer.get("email"),
    ]

    def party(caption, name, values):
        body = "<br/>".join(escape(str(v)) for v in values if v and str(v).strip())
        return [Paragraph(caption, label), Spacer(1, 1.5 * mm), _p(name, value), Paragraph(body, muted)]

    # Tarjetas con una franja de color a la izquierda (columna estrecha rellena).
    gap, accent = 8 * mm, 1 * mm
    card_w = (CONTENT_W - gap) / 2 - accent
    parties = Table(
        [["", party("EMISOR", seller.get("name"), seller_lines), "",
          "", party("FACTURAR A", buyer.get("name"), buyer_lines)]],
        colWidths=[accent, card_w, gap, accent, card_w],
    )
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, 0), GRANATE),
        ("BACKGROUND", (3, 0), (3, 0), AMARILLO),
        ("BACKGROUND", (1, 0), (1, 0), LIGHT_BG),
        ("BACKGROUND", (4, 0), (4, 0), LIGHT_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (3, 0), (3, 0), 0),
        ("RIGHTPADDING", (3, 0), (3, 0), 0),
    ]))
    story.append(parties)
    story.append(Spacer(1, 6 * mm))

    # ── Referencias del pedido ──────────────────────────────────────────────
    refs = [("PEDIDO", lines.get("order_number", "")),
            ("FORMA DE PAGO", lines.get("payment_method") or "Tarjeta")]
    if invoice.invoice_type == "corrective":
        refs.append(("RECTIFICA A", f"{lines.get('rectified_invoice_number', '')} "
                                    f"({lines.get('rectified_issued_on', '')})"))
    ref_table = Table(
        [[Paragraph(k, label) for k, _ in refs], [_p(v, value) for _, v in refs]],
        colWidths=[CONTENT_W / len(refs)] * len(refs),
    )
    ref_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(ref_table)
    if invoice.invoice_type == "corrective":
        story.append(Spacer(1, 2 * mm))
        story.append(_p(f"Motivo de la rectificación: {lines.get('reason') or 'Devolución'}", muted))
    story.append(Spacer(1, 6 * mm))

    # ── Líneas (importes con IVA incluido) ──────────────────────────────────
    rows = [[Paragraph("DESCRIPCIÓN", th), Paragraph("UDS.", th_c),
             Paragraph("PRECIO UNIT.", th_r), Paragraph("IMPORTE", th_r)]]
    for item in lines.get("items", []):
        rows.append([
            _p(item.get("description"), td),
            _p(item.get("quantity"), td_c),
            _p(_eur(item.get("unit_price", 0)), td_r),
            _p(_eur(item.get("total", 0)), td_r),
        ])
    items_table = Table(rows, colWidths=[CONTENT_W - 72 * mm, 16 * mm, 30 * mm, 26 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GRANATE),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 9),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, RULE),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 6 * mm))

    # ── Totales ─────────────────────────────────────────────────────────────
    totals = [("Subtotal productos", lines.get("subtotal", "0"))]
    if Decimal(str(lines.get("shipping", "0"))) != 0:
        totals.append(("Gastos de envío", lines["shipping"]))
    if Decimal(str(lines.get("discount", "0"))) != 0:
        name = f"Descuento ({lines['coupon_code']})" if lines.get("coupon_code") else "Descuento"
        totals.append((name, -Decimal(str(lines["discount"]))))
    totals.append(("Base imponible", invoice.tax_base))
    totals.append((f"IVA ({_pct(invoice.tax_rate)})", invoice.tax_amount))

    totals_rows = [[_p(k, tot_l), _p(_eur(v), tot_r)] for k, v in totals]
    totals_rows.append([Paragraph("TOTAL", grand_l), _p(_eur(invoice.total), grand_r)])
    last = len(totals_rows) - 1
    totals_table = Table(
        totals_rows,
        colWidths=[48 * mm, 32 * mm],
        style=TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("LINEBELOW", (0, last - 2), (-1, last - 2), 0.6, RULE),
            ("BACKGROUND", (0, last), (-1, last), GRANATE),
            ("TOPPADDING", (0, last), (-1, last), 8),
            ("BOTTOMPADDING", (0, last), (-1, last), 8),
        ]),
    )
    note = Paragraph(
        "Precios con IVA incluido.<br/>Gracias por confiar en CremaCuadrado.",
        style("thanks", fontName="Helvetica-Oblique", fontSize=9, leading=13, textColor=MUTED),
    )
    summary = Table([[note, totals_table]], colWidths=[CONTENT_W - 80 * mm, 80 * mm])
    summary.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "BOTTOM"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(summary)

    # ── Pie de página (en todas las páginas) ────────────────────────────────
    legal = (f"{seller.get('name', '')} · NIF {seller.get('nif', '')} · {seller.get('address', '')}, "
             f"{seller.get('postal_code', '')} {seller.get('city', '')}")
    registry = seller.get("registry") or ""

    def _footer(canvas, document):
        canvas.saveState()
        y = 16 * mm
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, y + 6 * mm, PAGE_W - MARGIN, y + 6 * mm)
        logo = _logo(6 * mm, color=MUTED)
        logo.drawOn(canvas, MARGIN, y - 1.5 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        x = MARGIN + 9 * mm
        canvas.drawString(x, y + 1.6 * mm, legal)
        canvas.drawString(x, y - 1.6 * mm,
                          (registry + " · " if registry else "")
                          + "Documento expedido electrónicamente, válido sin firma.")
        canvas.drawRightString(PAGE_W - MARGIN, y - 1.6 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
