"""
Invoice PDF generation service.
"""
import io
from datetime import datetime
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

# ---------------------------------------------------------------------------
# Company fiscal data (seller)
# ---------------------------------------------------------------------------

COMPANY = {
    "name": "Cremacuadrado SL",
    "cif": "B56673700",
    "address": "Camino del arca 18",
    "city": "Ciudad Real",
    "province": "Ciudad Real",
    "postal_code": "13005",
    "country": "España",
    "email": "Admin@cremacuadrado.com",
    "phone": "623 286 353",
}

GRANATE = colors.Color(0x7B / 255, 0x17 / 255, 0x16 / 255)
AMARILLO = colors.Color(0xE6 / 255, 0xC1 / 255, 0x5A / 255)
LIGHT_BG = colors.Color(0xF4 / 255, 0xF1 / 255, 0xE9 / 255)
CARD_BG = colors.Color(0xED / 255, 0xE9 / 255, 0xDF / 255)
INK = colors.Color(0x1C / 255, 0x1A / 255, 0x14 / 255)
MUTED = colors.Color(0x6B / 255, 0x64 / 255, 0x56 / 255)


def _invoice_number(order_number: str) -> str:
    """Derive invoice number from order number. CC-YYMMDD-XXXXXX → FAC-YYXXXXXX"""
    parts = order_number.split("-")
    if len(parts) == 3:
        return f"FAC-{parts[1]}{parts[2]}"
    return f"FAC-{order_number.replace('-', '')}"


def generate_invoice_pdf(order, customer_name: str, customer_email: str) -> bytes:
    """
    Generate a PDF invoice for the given order.
    Returns raw PDF bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    story = []

    # -----------------------------------------------------------------------
    # Styles
    # -----------------------------------------------------------------------
    h1 = ParagraphStyle("h1", fontSize=22, textColor=GRANATE, fontName="Helvetica-Bold",
                         spaceAfter=2, leading=26)
    h2 = ParagraphStyle("h2", fontSize=11, textColor=GRANATE, fontName="Helvetica-Bold",
                         spaceAfter=4)
    normal = ParagraphStyle("normal", fontSize=9, textColor=INK, fontName="Helvetica",
                             leading=13)
    muted = ParagraphStyle("muted", fontSize=8, textColor=MUTED, fontName="Helvetica",
                            leading=12)
    right_bold = ParagraphStyle("right_bold", fontSize=10, textColor=INK,
                                 fontName="Helvetica-Bold", alignment=TA_RIGHT)
    right_normal = ParagraphStyle("right_normal", fontSize=9, textColor=MUTED,
                                   fontName="Helvetica", alignment=TA_RIGHT)
    center_small = ParagraphStyle("center_small", fontSize=8, textColor=MUTED,
                                   fontName="Helvetica", alignment=TA_CENTER)

    invoice_number = _invoice_number(order.order_number)
    invoice_date = (order.paid_at or order.created_at).strftime("%d/%m/%Y")

    # -----------------------------------------------------------------------
    # Header block: brand + invoice title
    # -----------------------------------------------------------------------
    header_data = [
        [
            Paragraph("CREMACUADRADO", h1),
            Paragraph(f"FACTURA", ParagraphStyle("inv", fontSize=18, textColor=GRANATE,
                                                  fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        ],
        [
            Paragraph("crema de pistacho manchego artesanal", muted),
            Paragraph(f"Nº {invoice_number}", ParagraphStyle("invn", fontSize=10, textColor=MUTED,
                                                               fontName="Helvetica", alignment=TA_RIGHT)),
        ],
        [
            Paragraph("", normal),
            Paragraph(f"Fecha: {invoice_date}", right_normal),
        ],
    ]
    header_table = Table(header_data, colWidths=[100 * mm, 70 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=GRANATE, spaceAfter=8))

    # -----------------------------------------------------------------------
    # Seller + Buyer
    # -----------------------------------------------------------------------
    addr = order.shipping_address or {}
    buyer_name = customer_name or f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
    buyer_lines = [
        buyer_name,
        addr.get("street", ""),
        f"{addr.get('postal_code', '')} {addr.get('city', '')}",
        f"{addr.get('province', '')}, {addr.get('country', 'España')}",
        customer_email,
        addr.get("phone", ""),
    ]
    buyer_text = "<br/>".join(l for l in buyer_lines if l.strip())

    seller_lines = [
        COMPANY["name"],
        f"CIF: {COMPANY['cif']}",
        COMPANY["address"],
        f"{COMPANY['postal_code']} {COMPANY['city']}",
        f"{COMPANY['province']}, {COMPANY['country']}",
        COMPANY["email"],
        COMPANY["phone"],
    ]
    seller_text = "<br/>".join(seller_lines)

    parties_data = [
        [
            Paragraph("EMISOR", h2),
            Paragraph("", normal),
            Paragraph("CLIENTE", h2),
        ],
        [
            Paragraph(seller_text, normal),
            Paragraph("", normal),
            Paragraph(buyer_text, normal),
        ],
    ]
    parties_table = Table(parties_data, colWidths=[80 * mm, 10 * mm, 80 * mm])
    parties_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (0, 1), CARD_BG),
        ("BACKGROUND", (2, 1), (2, 1), CARD_BG),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("TOPPADDING", (0, 1), (-1, 1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 1), (0, 1), 8),
        ("RIGHTPADDING", (2, 1), (2, 1), 8),
        ("LEFTPADDING", (2, 1), (2, 1), 8),
    ]))
    story.append(parties_table)
    story.append(Spacer(1, 6 * mm))

    # -----------------------------------------------------------------------
    # Order info pill
    # -----------------------------------------------------------------------
    order_ref_data = [[
        Paragraph(f"<b>Pedido:</b> {order.order_number}", normal),
        Paragraph(f"<b>Método de pago:</b> {order.payment_method or 'Tarjeta'}", normal),
    ]]
    order_ref_table = Table(order_ref_data, colWidths=[85 * mm, 85 * mm])
    order_ref_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, MUTED),
    ]))
    story.append(order_ref_table)
    story.append(Spacer(1, 5 * mm))

    # -----------------------------------------------------------------------
    # Items table
    # -----------------------------------------------------------------------
    item_header = [
        Paragraph("DESCRIPCIÓN", ParagraphStyle("th", fontSize=9, textColor=colors.white,
                                                  fontName="Helvetica-Bold")),
        Paragraph("UDS", ParagraphStyle("th_c", fontSize=9, textColor=colors.white,
                                         fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Paragraph("PRECIO UNIT.", ParagraphStyle("th_r", fontSize=9, textColor=colors.white,
                                                   fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        Paragraph("TOTAL", ParagraphStyle("th_r", fontSize=9, textColor=colors.white,
                                           fontName="Helvetica-Bold", alignment=TA_RIGHT)),
    ]
    item_rows = [item_header]

    for item in order.items:
        item_rows.append([
            Paragraph(item.product_name, normal),
            Paragraph(str(item.quantity), ParagraphStyle("c", fontSize=9, textColor=INK,
                                                           fontName="Helvetica", alignment=TA_CENTER)),
            Paragraph(f"{float(item.unit_price):.2f} €", ParagraphStyle("r", fontSize=9,
                                                                          textColor=INK, fontName="Helvetica",
                                                                          alignment=TA_RIGHT)),
            Paragraph(f"{float(item.total):.2f} €", ParagraphStyle("r", fontSize=9,
                                                                     textColor=INK, fontName="Helvetica",
                                                                     alignment=TA_RIGHT)),
        ])

    items_table = Table(item_rows, colWidths=[100 * mm, 15 * mm, 30 * mm, 25 * mm])
    item_style = TableStyle([
        # Header row
        ("BACKGROUND", (0, 0), (-1, 0), GRANATE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 8),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MUTED),
    ])
    items_table.setStyle(item_style)
    story.append(items_table)
    story.append(Spacer(1, 5 * mm))

    # -----------------------------------------------------------------------
    # Totals block
    # -----------------------------------------------------------------------
    subtotal = float(order.subtotal)
    shipping = float(order.shipping_cost)
    discount = float(order.discount)
    tax = float(order.tax)
    total = float(order.total)

    totals_rows = []
    totals_rows.append(["Subtotal", f"{subtotal:.2f} €"])
    if shipping > 0:
        totals_rows.append(["Gastos de envío", f"{shipping:.2f} €"])
    if discount > 0:
        label = f"Descuento ({order.coupon_code})" if order.coupon_code else "Descuento"
        totals_rows.append([label, f"−{discount:.2f} €"])

    # Derive base imponible and IVA from tax (21% included in total)
    base_imponible = total - tax
    totals_rows.append([f"Base imponible", f"{base_imponible:.2f} €"])
    totals_rows.append(["IVA (21%)", f"{tax:.2f} €"])

    totals_data = [[
        Paragraph("", normal),
        Table(
            [[Paragraph(r[0], muted), Paragraph(r[1], right_normal)] for r in totals_rows]
            + [[Paragraph("TOTAL", ParagraphStyle("tb", fontSize=12, textColor=GRANATE,
                                                   fontName="Helvetica-Bold")),
                Paragraph(f"{total:.2f} €", ParagraphStyle("tr", fontSize=12, textColor=GRANATE,
                                                             fontName="Helvetica-Bold", alignment=TA_RIGHT))]],
            colWidths=[50 * mm, 30 * mm],
            style=TableStyle([
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, GRANATE),
                ("TOPPADDING", (0, -1), (-1, -1), 6),
            ]),
        ),
    ]]
    totals_outer = Table(totals_data, colWidths=[90 * mm, 80 * mm])
    story.append(totals_outer)
    story.append(Spacer(1, 10 * mm))

    # -----------------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------------
    story.append(HRFlowable(width="100%", thickness=0.5, color=MUTED, spaceAfter=4))
    story.append(Paragraph(
        "Esta factura ha sido generada electrónicamente y es válida sin firma. "
        "Conserve este documento como justificante de compra.",
        center_small,
    ))
    story.append(Spacer(1, 2))
    story.append(Paragraph(
        f"{COMPANY['name']} · CIF {COMPANY['cif']} · {COMPANY['address']}, "
        f"{COMPANY['postal_code']} {COMPANY['city']} · {COMPANY['email']}",
        center_small,
    ))

    doc.build(story)
    return buf.getvalue()
