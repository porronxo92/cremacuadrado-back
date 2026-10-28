"""
Email service — SMTP with inline-CSS HTML templates.
Set EMAIL_ENABLED=True in .env and configure SMTP_* variables to send real emails.

Outgoing mail routes through one of two mailboxes (see app/config.py):
  "pedidos" — Pedidos@cremacuadrado.com: confirmación de pedido, fallo de pago,
              actualización de envío, factura
  "info"    — Info@cremacuadrado.com: bienvenida, verificación, seguridad, y
              cualquier otro email que no sea de pedidos (newsletter, contacto, B2B...)
"""
import logging
import smtplib
from html import escape
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.config import settings
from app.utils.site import site_url

logger = logging.getLogger("cremacuadrado.email")

MailboxKind = Literal["pedidos", "info"]

_MADRID = ZoneInfo("Europe/Madrid")


def _madrid(dt: datetime) -> datetime:
    """Hora peninsular para mostrar en los emails. Las fechas de la BBDD son UTC
    (naive) y el servidor de Vercel corre en UTC: sin convertir salían 1–2 h antes."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_MADRID)


# ---------------------------------------------------------------------------
# Mailbox routing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Mailbox:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str

    @property
    def sender(self) -> str:
        return f"{self.from_name} <{self.from_email}>"


def _info_mailbox() -> _Mailbox:
    return _Mailbox(
        host=settings.SMTP_INFO_HOST,
        port=settings.SMTP_INFO_PORT,
        user=settings.SMTP_INFO_USER,
        password=settings.SMTP_INFO_PASSWORD,
        from_email=settings.SMTP_INFO_FROM_EMAIL,
        from_name=settings.SMTP_INFO_FROM_NAME,
    )


def _mailbox(kind: MailboxKind) -> _Mailbox:
    """Resolve SMTP credentials for the given category.

    Pedidos@ falls back to Info@ if its dedicated credentials haven't been
    configured yet in .env, so order emails keep working meanwhile.
    """
    if kind == "pedidos":
        if settings.SMTP_PEDIDOS_USER and settings.SMTP_PEDIDOS_PASSWORD:
            return _Mailbox(
                host=settings.SMTP_PEDIDOS_HOST,
                port=settings.SMTP_PEDIDOS_PORT,
                user=settings.SMTP_PEDIDOS_USER,
                password=settings.SMTP_PEDIDOS_PASSWORD,
                from_email=settings.SMTP_PEDIDOS_FROM_EMAIL,
                from_name=settings.SMTP_PEDIDOS_FROM_NAME,
            )
        logger.warning("SMTP_PEDIDOS_USER/PASSWORD not set, falling back to Info@ mailbox for pedidos email")
    return _info_mailbox()


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def _send(to_email: str, subject: str, html: str, text: str = "", mailbox: MailboxKind = "info") -> bool:
    box = _mailbox(mailbox)

    if not settings.EMAIL_ENABLED:
        logger.info("Email suppressed (EMAIL_ENABLED=False) mailbox=%s to=%s subject=%r", mailbox, to_email, subject)
        return True

    # ------------------------------------------------------------------
    # Resend path (production) — used when RESEND_API_KEY is set.
    # Resend works from any cloud provider; SMTP from Hostinger is blocked
    # by AWS/Vercel IP ranges.
    # ------------------------------------------------------------------
    if settings.RESEND_API_KEY:
        try:
            import resend as _resend
            _resend.api_key = settings.RESEND_API_KEY
            params: dict = {
                "from": box.sender,
                "to": [to_email],
                "subject": subject,
                "html": html,
            }
            if text:
                params["text"] = text
            _resend.Emails.send(params)
            logger.info("Email sent via Resend mailbox=%s to=%s subject=%r", mailbox, to_email, subject)
            return True
        except Exception:
            logger.error("Email failed via Resend mailbox=%s to=%s subject=%r", mailbox, to_email, subject, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # SMTP path (local development)
    # ------------------------------------------------------------------
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = box.sender
        msg["To"] = to_email
        if text:
            msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(box.host, box.port) as server:
            server.ehlo()
            server.starttls()
            server.login(box.user, box.password)
            server.sendmail(box.from_email, [to_email], msg.as_string())

        logger.info("Email sent via SMTP mailbox=%s to=%s subject=%r", mailbox, to_email, subject)
        return True
    except Exception:
        logger.error("Email failed via SMTP mailbox=%s to=%s subject=%r", mailbox, to_email, subject, exc_info=True)
        return False


def _send_with_attachment(
    to_email: str,
    subject: str,
    html: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    text: str = "",
    mailbox: MailboxKind = "info",
) -> bool:
    box = _mailbox(mailbox)

    if not settings.EMAIL_ENABLED:
        logger.info(
            "Email suppressed (EMAIL_ENABLED=False) mailbox=%s to=%s subject=%r attachment=%s",
            mailbox, to_email, subject, attachment_filename,
        )
        return True

    # ------------------------------------------------------------------
    # Resend path (production)
    # ------------------------------------------------------------------
    if settings.RESEND_API_KEY:
        try:
            import base64 as _b64
            import resend as _resend
            _resend.api_key = settings.RESEND_API_KEY
            params: dict = {
                "from": box.sender,
                "to": [to_email],
                "subject": subject,
                "html": html,
                "attachments": [
                    {
                        "filename": attachment_filename,
                        "content": _b64.b64encode(attachment_bytes).decode(),
                    }
                ],
            }
            if text:
                params["text"] = text
            _resend.Emails.send(params)
            logger.info("Email with attachment sent via Resend mailbox=%s to=%s subject=%r", mailbox, to_email, subject)
            return True
        except Exception:
            logger.error("Email with attachment failed via Resend mailbox=%s to=%s subject=%r", mailbox, to_email, subject, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # SMTP path (local development)
    # ------------------------------------------------------------------
    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = box.sender
        msg["To"] = to_email

        body = MIMEMultipart("alternative")
        if text:
            body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body)

        pdf_part = MIMEApplication(attachment_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(pdf_part)

        with smtplib.SMTP(box.host, box.port) as server:
            server.ehlo()
            server.starttls()
            server.login(box.user, box.password)
            server.sendmail(box.from_email, [to_email], msg.as_string())

        logger.info("Email with attachment sent via SMTP mailbox=%s to=%s subject=%r", mailbox, to_email, subject)
        return True
    except Exception:
        logger.error("Email with attachment failed via SMTP mailbox=%s to=%s subject=%r", mailbox, to_email, subject, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Shared HTML layout
# ---------------------------------------------------------------------------

def _wrap_layout(inner_html: str, unsubscribe_url: Optional[str] = None) -> str:
    unsubscribe = ""
    if unsubscribe_url:
        unsubscribe = (
            '<p style="margin:12px 0 0;font-family:Arial,sans-serif;font-size:11px;color:#6B6456;">'
            "Recibes este email porque te suscribiste a las novedades de CremaCuadrado. "
            f'<a href="{unsubscribe_url}" style="color:#E6C15A;">Darte de baja</a>'
            "</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CremaCuadrado</title>
</head>
<body style="margin:0;padding:0;background-color:#F4F1E9;font-family:Georgia,'Times New Roman',serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#F4F1E9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

          <!-- HEADER -->
          <tr>
            <td style="background-color:#7B1716;padding:32px 40px;text-align:center;border-radius:8px 8px 0 0;">
              <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;letter-spacing:4px;text-transform:uppercase;color:#E6C15A;">crema de pistacho manchego</p>
              <h1 style="margin:8px 0 0;font-family:Arial,sans-serif;font-size:28px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#F4F1E9;">CREMACUADRADO</h1>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="background-color:#ffffff;padding:40px 40px 32px;border-left:1px solid #e8e3d8;border-right:1px solid #e8e3d8;">
              {inner_html}
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background-color:#1C1A14;padding:24px 40px;border-radius:0 0 8px 8px;text-align:center;">
              <p style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;">
                CremaCuadrado · Crema de pistacho manchego artesanal
              </p>
              <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#4a453d;">
                <a href="{site_url()}/privacidad" style="color:#6B6456;text-decoration:none;">Privacidad</a>
                &nbsp;·&nbsp;
                <a href="{site_url()}/condiciones-venta" style="color:#6B6456;text-decoration:none;">Condiciones de venta</a>
                &nbsp;·&nbsp;
                <a href="{site_url()}/devoluciones" style="color:#6B6456;text-decoration:none;">Devoluciones</a>
              </p>
              {unsubscribe}
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


_MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _btn(url: str, label: str) -> str:
    return f"""<p style="text-align:center;margin:32px 0 0;">
  <a href="{escape(url)}"
     style="display:inline-block;padding:14px 32px;background-color:#7B1716;color:#F4F1E9;
            font-family:Arial,sans-serif;font-size:14px;font-weight:600;text-decoration:none;
            border-radius:24px;letter-spacing:0.5px;">
    {label}
  </a>
</p>"""


# ---------------------------------------------------------------------------
# Order confirmation email
# ---------------------------------------------------------------------------

class OrderEmailData:
    """Structured data for the order confirmation email."""

    def __init__(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        order_date: datetime,
        items: list,           # list of dicts: {name, qty, unit_price, total}
        subtotal: Decimal,
        shipping_cost: Decimal,
        discount: Decimal,
        total: Decimal,
        shipping_address: dict,
        coupon_code: Optional[str] = None,
        customer_notes: Optional[str] = None,
        tracking_number: Optional[str] = None,
        invoice_number: Optional[str] = None,
        invoice_pdf: Optional[bytes] = None,
        is_guest: bool = False,
    ):
        self.is_guest = is_guest  # pedido sin cuenta (user_id NULL)
        self.to_email = to_email
        self.customer_name = customer_name
        self.order_number = order_number
        self.order_date = order_date
        self.items = items
        self.subtotal = subtotal
        self.shipping_cost = shipping_cost
        self.discount = discount
        self.total = total
        self.shipping_address = shipping_address
        self.coupon_code = coupon_code
        self.customer_notes = customer_notes
        self.tracking_number = tracking_number
        self.invoice_number = invoice_number
        self.invoice_pdf = invoice_pdf


def send_order_confirmation(data: OrderEmailData) -> bool:
    order_url = f"{site_url()}/account/orders"
    date_str = _madrid(data.order_date).strftime("%d/%m/%Y %H:%M")

    # Items rows
    items_rows = ""
    for item in data.items:
        items_rows += f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">
            {escape(str(item['name']))}
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#6B6456;text-align:center;">
            {item['qty']}
          </td>
          <td style="padding:12px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#6B6456;text-align:right;">
            {item['unit_price']:.2f} €
          </td>
          <td style="padding:12px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;font-weight:600;color:#1C1A14;text-align:right;">
            {item['total']:.2f} €
          </td>
        </tr>"""

    # Totals rows
    shipping_label = "Envío gratuito" if data.shipping_cost == 0 else f"{data.shipping_cost:.2f} €"
    discount_row = ""
    if data.discount and data.discount > 0:
        coupon_label = f" ({escape(data.coupon_code)})" if data.coupon_code else ""
        discount_row = f"""
        <tr>
          <td colspan="3" style="padding:6px 0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;text-align:right;">
            Descuento{coupon_label}
          </td>
          <td style="padding:6px 0;font-family:Arial,sans-serif;font-size:13px;color:#27ae60;text-align:right;">
            −{data.discount:.2f} €
          </td>
        </tr>"""

    # Shipping address
    addr = data.shipping_address
    full_name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
    addr_lines = [
        full_name,
        addr.get("street", ""),
        f"{addr.get('postal_code', '')} {addr.get('city', '')}".strip(),
        addr.get("province", ""),
        addr.get("country", ""),
    ]
    addr_html = "<br>".join(escape(str(line)) for line in addr_lines if line)

    # Tracking (Correos) — present only when a localizador was generated
    tracking_block = ""
    info_text = (
        "Preparamos tu pedido y te escribimos otra vez cuando salga, con el "
        "número de seguimiento de Correos. La entrega suele tardar "
        "<strong>2–4 días hábiles</strong>."
    )
    if data.tracking_number:
        track_url = f"https://www.correos.es/es/es/herramientas/localizador/envios/detalle?tracking-number={data.tracking_number}"
        tracking_block = f"""
        <hr style="border:none;border-top:1px solid #E8E3D8;margin:32px 0;">
        <p style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;font-weight:600;">Seguimiento del envío</p>
        <div style="padding:16px;background-color:#F4F1E9;border-radius:6px;text-align:center;">
          <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;text-transform:uppercase;letter-spacing:1px;">Número de seguimiento Correos</p>
          <p style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:20px;font-weight:700;color:#1C1A14;letter-spacing:2px;">{data.tracking_number}</p>
          {_btn(track_url, "Seguir en Correos")}
        </div>"""
        info_text = (
            "Estamos preparando tu pedido. Con el número de seguimiento de arriba "
            "puedes ver por dónde va. La entrega suele tardar "
            "<strong>2–4 días hábiles</strong>."
        )

    # Notes
    notes_block = ""
    if data.customer_notes:
        notes_block = f"""
        <div style="margin-top:24px;padding:16px;background-color:#F4F1E9;border-radius:6px;border-left:3px solid #E6C15A;">
          <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;">Notas del pedido</p>
          <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">{escape(data.customer_notes)}</p>
        </div>"""

    # Enlace firmado de descarga de la factura (sirve también a invitados).
    from app.utils.signing import invoice_token
    invoice_url = f"{site_url()}/factura?pedido={data.order_number}&token={invoice_token(data.order_number)}"
    if data.is_guest:
        # Invitado: sin «Ver mis pedidos» (no tiene cuenta). Acción principal: la factura;
        # secundaria: crear cuenta con el mismo email para ver sus pedidos.
        register_url = f"{site_url()}/auth/register?email={quote(data.to_email, safe='')}"
        invoice_note = ""
        main_action = f"""
      <p style="margin:24px 0 0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
        Guarda este email: con el botón de abajo puedes descargar la factura cuando quieras.
      </p>
      {_btn(invoice_url, "Descargar mi factura")}
      <div style="margin-top:32px;padding:16px;background-color:#F4F1E9;border-radius:6px;">
        <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;line-height:1.6;">
          Para seguir tus pedidos, crea una cuenta con este mismo email
          (<strong>{escape(data.to_email)}</strong>) y confírmalo con el enlace que te enviaremos.
          Tus pedidos aparecerán en «Mis pedidos».
          <a href="{escape(register_url)}" style="color:#7B1716;">Crear mi cuenta</a>
        </p>
      </div>"""
        text_tail = f"Descarga tu factura: {invoice_url}\nCrea una cuenta con este email para ver tus pedidos: {register_url}"
    else:
        invoice_note = f"""
      <p style="margin:24px 0 0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
        Puedes <a href="{invoice_url}" style="color:#7B1716;">descargar la factura de tu compra aquí</a>
        o desde «Mis pedidos» si tienes cuenta.
      </p>"""
        main_action = _btn(order_url, "Ver mis pedidos")
        text_tail = f"Puedes ver los detalles en: {order_url}"

    inner = f"""
      <!-- Greeting -->
      <h2 style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:22px;font-weight:700;color:#7B1716;">
        Gracias por tu pedido, {escape(data.customer_name)}
      </h2>
      <p style="margin:0 0 28px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;line-height:1.5;">
        Hemos recibido tu pedido y ya lo estamos preparando.
      </p>

      <!-- Order meta pill row -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
        <tr>
          <td style="width:50%;padding:16px;background-color:#F4F1E9;border-radius:6px 0 0 6px;border:1px solid #E8E3D8;">
            <p style="margin:0 0 2px;font-family:Arial,sans-serif;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;">Número de pedido</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:16px;font-weight:700;color:#7B1716;">{data.order_number}</p>
          </td>
          <td width="2" style="background-color:#E8E3D8;"></td>
          <td style="width:50%;padding:16px;background-color:#F4F1E9;border-radius:0 6px 6px 0;border:1px solid #E8E3D8;border-left:none;">
            <p style="margin:0 0 2px;font-family:Arial,sans-serif;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;">Fecha</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:15px;font-weight:600;color:#1C1A14;">{date_str}</p>
          </td>
        </tr>
      </table>

      <!-- Items table -->
      <p style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;font-weight:600;">Productos</p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <thead>
          <tr style="border-bottom:2px solid #1C1A14;">
            <th style="padding:0 0 8px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#6B6456;text-align:left;font-weight:600;">Producto</th>
            <th style="padding:0 8px 8px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#6B6456;text-align:center;font-weight:600;">Uds.</th>
            <th style="padding:0 0 8px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#6B6456;text-align:right;font-weight:600;">Precio</th>
            <th style="padding:0 0 8px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#6B6456;text-align:right;font-weight:600;">Total</th>
          </tr>
        </thead>
        <tbody>
          {items_rows}
        </tbody>
      </table>

      <!-- Totals -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
        <tr>
          <td colspan="3" style="padding:8px 0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;text-align:right;">Subtotal</td>
          <td style="padding:8px 0;font-family:Arial,sans-serif;font-size:13px;color:#1C1A14;text-align:right;">{data.subtotal:.2f} €</td>
        </tr>
        <tr>
          <td colspan="3" style="padding:4px 0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;text-align:right;">Envío</td>
          <td style="padding:4px 0;font-family:Arial,sans-serif;font-size:13px;color:#1C1A14;text-align:right;">{shipping_label}</td>
        </tr>
        {discount_row}
        <tr style="border-top:2px solid #1C1A14;">
          <td colspan="3" style="padding:12px 0 0;font-family:Arial,sans-serif;font-size:16px;font-weight:700;color:#1C1A14;text-align:right;">Total</td>
          <td style="padding:12px 0 0;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#7B1716;text-align:right;">{data.total:.2f} €</td>
        </tr>
      </table>

      <!-- Divider -->
      <hr style="border:none;border-top:1px solid #E8E3D8;margin:32px 0;">

      <!-- Shipping address -->
      <p style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;font-weight:600;">Dirección de envío</p>
      <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;line-height:1.8;">
        {addr_html}
      </p>

      {tracking_block}

      {notes_block}

      <!-- Info box -->
      <div style="margin-top:32px;padding:20px;background-color:#FFF9ED;border-radius:6px;border:1px solid #E6C15A;">
        <p style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:14px;font-weight:600;color:#1C1A14;">Qué pasa ahora</p>
        <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;line-height:1.6;">
          {info_text}
        </p>
      </div>

      {invoice_note}

      {main_action}
    """

    html = _wrap_layout(inner)
    subject = f"Pedido {data.order_number} confirmado · CremaCuadrado"

    text = (
        f"Hola {data.customer_name}, hemos confirmado tu pedido {data.order_number}.\n"
        f"Total: {data.total:.2f} €\n"
        f"{text_tail}"
    )

    if data.invoice_pdf and data.invoice_number:
        return _send_with_attachment(
            to_email=data.to_email,
            subject=subject,
            html=html,
            attachment_bytes=data.invoice_pdf,
            attachment_filename=f"Factura_{data.invoice_number}.pdf",
            text=text,
            mailbox="pedidos",
        )
    return _send(data.to_email, subject, html, text, mailbox="pedidos")


# ---------------------------------------------------------------------------
# Legacy method (used in non-webhook paths) — delegates to send_order_confirmation
# ---------------------------------------------------------------------------

class EmailService:

    @staticmethod
    def send_email(to_email: str, subject: str, html_content: str, text_content: str = "", mailbox: MailboxKind = "info") -> bool:
        return _send(to_email, subject, html_content, text_content, mailbox=mailbox)

    @classmethod
    def send_welcome_email(cls, to_email: str, first_name: str) -> bool:
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Bienvenido, {escape(first_name or "")}</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Gracias por registrarte en CremaCuadrado. Tu cuenta ya está activa. Tienes la crema Pura y la Crunchy en formatos de 100 g, 200 g y 1 kg.
          </p>
          {_btn(site_url() + "/tienda", "Ver productos")}
        """
        return _send(to_email, "Bienvenido a CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_legacy_welcome_email(cls, to_email: str, first_name: str, coupon_code: str,
                                  valid_from: datetime, valid_until: datetime) -> bool:
        """Bienvenida de los clientes de la web antigua (fundadores), con su cupón personal del 20 % (services/legacy_customers.py)."""
        until = _madrid(valid_until)
        until_long = f"{until.day} de {_MONTHS_ES[until.month - 1]} de {until.year}"
        until_short = until.strftime("%d/%m/%Y")
        hello = f"Hola, {escape(first_name)}" if first_name else "Hola"
        code = escape(coupon_code)
        h2 = 'style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:18px;color:#7B1716;"'
        txt = 'style="margin:0;font-family:Arial,sans-serif;font-size:16px;color:#1C1A14;line-height:1.6;"'
        lst = 'style="margin:0;padding-left:20px;font-family:Arial,sans-serif;font-size:16px;color:#1C1A14;line-height:1.6;"'
        wa = settings.WHATSAPP_PHONE
        wa_display = f"{wa[2:5]} {wa[5:7]} {wa[7:9]} {wa[9:]}" if len(wa) == 11 else wa
        inner = f"""
          <h2 style="margin:0 0 14px;font-family:Arial,sans-serif;font-size:24px;line-height:1.25;color:#7B1716;">Estrenamos web y el precio de los tarros sube</h2>
          <p {txt}>{hello}. Eres de las personas que probaron CremaCuadrado desde el principio y queremos darte las gracias:
            te hemos reservado un <strong>20&nbsp;% de descuento</strong>. Con la nueva web <strong>sube el precio de los tarros de 200&nbsp;g y 100&nbsp;g</strong>,
            y queremos contarte por qué.</p>

          <h3 {h2.replace("0 0 8px", "24px 0 8px")}>Por qué sube</h3>
          <ul {lst}>
            <li>Desde 2024, el precio del pistacho manchego no ha parado de subir: <strong>es de calidad, sostenible y europeo, y todo el mundo lo quiere</strong>.</li>
            <li>Cada año hemos aguantado <strong>cerca de un 20&nbsp;%</strong> de aumento.</li>
            <li>Este año habrá <strong>poco pistacho en todo el mundo</strong> por el clima en los países que más exportan a Europa y España: <strong>Estados Unidos, Irán y Turquía</strong>.</li>
            <li>Apostamos por lo que nos distingue, <strong>nuestra forma de hacer la crema</strong>, para que el sabor del pistacho llegue intacto al tarro y podamos <strong>seguir mejorando</strong> sin perder lo que somos.</li>
          </ul>

          <h3 {h2.replace("0 0 8px", "24px 0 8px")}>Para ti, que estás desde el principio</h3>
          <p {txt}>Queremos que formes parte de esta etapa y que sigas comprando a los precios de siempre:</p>
          <ul {lst}>
            <li><strong>20&nbsp;% de descuento</strong> en los tarros de <strong>200&nbsp;g y 100&nbsp;g</strong>.</li>
            <li><strong>Todas las veces que quieras</strong>, hasta el <strong>{until_long}</strong>.</li>
          </ul>

          <div style="margin:20px 0;padding:20px 12px;background-color:#F4F1E9;border-radius:8px;text-align:center;border:1.5px dashed #7B1716;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;text-transform:uppercase;letter-spacing:2px;">Tu código personal</p>
            <p style="margin:6px 0;font-family:Arial,sans-serif;font-size:26px;font-weight:700;color:#7B1716;letter-spacing:3px;">{code}</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">Tarros de 200&nbsp;g y 100&nbsp;g · hasta el {until_short}</p>
          </div>

          <h3 {h2}>Cómo usarlo</h3>
          <p {txt}>Tu cuenta ya está lista. Inicia sesión con este email e introduce <strong>{code}</strong> al pagar.
            Solo funciona en tu cuenta y no se suma a otros códigos. También lo tienes en «Mi cuenta → Mi perfil».</p>
          {_btn(site_url() + "/tienda", "Ir a la tienda")}

          <div style="margin:28px 0 0;padding:16px 18px;background-color:#F4F1E9;border-radius:8px;font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.55;">
            <strong>Seguimos siendo dos personas, Lucas y Stefano.</strong><br>
            Escríbenos por <a href="https://wa.me/{escape(wa)}" style="color:#7B1716;font-weight:700;">WhatsApp al {escape(wa_display)}</a> cuando quieras:
            preguntas, ideas o lo que opines de la crema. <strong>Lo leemos nosotros</strong>, sin intermediarios.
          </div>

          <p style="margin:24px 0 0;font-family:Arial,sans-serif;font-size:16px;color:#1C1A14;line-height:1.6;">
            <strong>Gracias por estar desde el principio. Nos da mucha energía hacer algo que gusta. Juntos vamos a por más.</strong><br><br>
            Un abrazo,<br><strong>Lucas y Stefano</strong><br>
            <span style="color:#6B6456;font-size:14px;">CremaCuadrado · Ciudad Real</span>
          </p>
        """
        return _send(to_email, "Querido socio, un 20% para ti", _wrap_layout(inner))

    @classmethod
    def send_newsletter_confirmation_email(cls, to_email: str, confirm_url: str) -> bool:
        """Doble opt-in: el alta solo es efectiva cuando la persona confirma su email."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Confirma tu suscripción</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Has pedido recibir recetas, novedades y ofertas de CremaCuadrado. Confírmalo con el botón
            y te enviamos un código de <strong>10% de descuento</strong> para tu primer pedido.
          </p>
          {_btn(confirm_url, "Confirmar suscripción")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;line-height:1.6;">
            Si no has sido tú, ignora este email y no te escribiremos más.
            Responsable: CREMACUADRADO SL. Finalidad: envío de comunicaciones comerciales.
            Puedes darte de baja en cualquier momento. Más información en
            <a href="{site_url()}/privacidad" style="color:#7B1716;">{site_url()}/privacidad</a>.
          </p>
        """
        return _send(to_email, "Confirma tu suscripción · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_newsletter_welcome_email(cls, to_email: str, coupon_code: str, unsubscribe_url: Optional[str] = None) -> bool:
        """Cupón de bienvenida tras confirmar la suscripción (doble opt-in)."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Suscripción confirmada</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Gracias por confirmar. Este es tu código de <strong>10% de descuento en tu primer pedido</strong>.
          </p>
          <div style="margin:24px 0;padding:20px;background-color:#F4F1E9;border-radius:6px;text-align:center;border:1px dashed #7B1716;">
            <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;text-transform:uppercase;letter-spacing:1px;">Tu código de descuento</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:24px;font-weight:700;color:#7B1716;letter-spacing:3px;">{coupon_code}</p>
          </div>
          {_btn(site_url() + "/tienda", "Ir a la tienda")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Aplícalo en el carrito antes de pagar. Vale para tu primer pedido con una cuenta registrada.
          </p>
        """
        return _send(to_email, "Tu código del 10% · CremaCuadrado", _wrap_layout(inner, unsubscribe_url))

    @classmethod
    def send_withdrawal_ack(cls, to_email: str, full_name: str, order_number: str,
                            request_id: int, items_text: Optional[str], requested_at: datetime) -> bool:
        """Acuse de recibo del desistimiento en soporte duradero (TRLGDCU art. 106.3)."""
        items = escape(items_text or "Pedido completo")
        full_name = escape(full_name)
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Hemos recibido tu desistimiento</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola {full_name}, confirmamos la recepción de tu solicitud de desistimiento.
          </p>
          <table style="width:100%;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;margin:16px 0;">
            <tr><td style="padding:4px 0;color:#6B6456;">Nº de solicitud</td><td style="padding:4px 0;"><strong>D-{request_id:06d}</strong></td></tr>
            <tr><td style="padding:4px 0;color:#6B6456;">Pedido</td><td style="padding:4px 0;">{order_number}</td></tr>
            <tr><td style="padding:4px 0;color:#6B6456;">Productos</td><td style="padding:4px 0;">{items}</td></tr>
            <tr><td style="padding:4px 0;color:#6B6456;">Fecha de la solicitud</td><td style="padding:4px 0;">{_madrid(requested_at).strftime("%d/%m/%Y %H:%M")} (hora peninsular)</td></tr>
          </table>
          <p style="font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;line-height:1.6;">
            En breve te escribiremos con las instrucciones para devolver los productos. Te reembolsaremos
            todos los pagos recibidos, incluidos los gastos de envío ordinarios, en un plazo máximo de
            14 días naturales desde hoy, por el mismo medio de pago. Podemos retener el reembolso hasta
            recibir los productos o hasta que acredites su devolución.
          </p>
        """
        return _send(to_email, f"Desistimiento recibido · pedido {order_number}", _wrap_layout(inner), mailbox="pedidos")

    @classmethod
    def send_admin_withdrawal(cls, order_number: str, email: str, full_name: str,
                              items_text: Optional[str], reason: Optional[str], within_term: bool) -> bool:
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:20px;color:#7B1716;">Nueva solicitud de desistimiento</h2>
          <p style="font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;line-height:1.7;">
            <strong>Pedido:</strong> {order_number}<br>
            <strong>Cliente:</strong> {escape(full_name)} ({escape(email)})<br>
            <strong>Productos:</strong> {escape(items_text or "Pedido completo")}<br>
            <strong>Motivo:</strong> {escape(reason or "—")}<br>
            <strong>Dentro de plazo (14 días):</strong> {"Sí" if within_term else "NO, revisar"}
          </p>
          {_btn(site_url() + "/admin/desistimientos", "Ver en el panel")}
        """
        return _send(settings.ADMIN_EMAIL, f"Desistimiento · pedido {order_number}", _wrap_layout(inner), mailbox="pedidos")

    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_token: str) -> bool:
        reset_url = f"{site_url()}/auth/reset-password?token={reset_token}"
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Restablecer contraseña</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Has pedido restablecer tu contraseña. Pulsa el botón y crea una nueva. El enlace caduca en 1 hora.
          </p>
          {_btn(reset_url, "Restablecer contraseña")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Si no lo has pedido tú, ignora este email.
          </p>
        """
        return _send(to_email, "Restablecer contraseña · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_order_confirmation_email(
        cls,
        to_email: str,
        order_number: str,
        customer_name: str,
        total: str,
        items_html: str,
    ) -> bool:
        """Legacy signature — kept for compatibility. Prefer send_order_confirmation()."""
        inner = f"""
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Gracias por tu pedido, {escape(customer_name)}</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Pedido <strong>{order_number}</strong></p>
          <div style="font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">{items_html}</div>
          <p style="margin-top:16px;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#7B1716;">Total: {total}</p>
          {_btn(site_url() + "/account/orders", "Ver mi pedido")}
        """
        return _send(
            to_email,
            f"Pedido {order_number} confirmado · CremaCuadrado",
            _wrap_layout(inner),
            mailbox="pedidos",
        )

    @classmethod
    def send_order_shipped_email(
        cls, to_email: str, order_number: str, customer_name: str, tracking_number: Optional[str] = None
    ) -> bool:
        tracking_block = ""
        if tracking_number:
            track_url = f"https://www.correos.es/es/es/herramientas/localizador/envios/detalle?tracking-number={tracking_number}"
            tracking_block = f"""
              <div style="margin:24px 0;padding:16px;background-color:#F4F1E9;border-radius:6px;text-align:center;">
                <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:12px;color:#6B6456;text-transform:uppercase;letter-spacing:1px;">Número de seguimiento</p>
                <p style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:20px;font-weight:700;color:#1C1A14;letter-spacing:2px;">{tracking_number}</p>
                {_btn(track_url, "Seguir en Correos")}
              </div>"""
        inner = f"""
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Tu pedido ya está en camino, {escape(customer_name)}</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Pedido <strong>{order_number}</strong> · Entrega estimada: 2–4 días hábiles</p>
          {tracking_block}
        """
        return _send(to_email, f"Tu pedido {order_number} ya está en camino · CremaCuadrado", _wrap_layout(inner), mailbox="pedidos")

    @classmethod
    def send_pending_payment_reminder(
        cls, to_email: str, first_name: str, order_number: str, items: list, total: Decimal,
    ) -> bool:
        """Recordatorio de un pedido iniciado y sin pagar. Transaccional: sin cupones ni promociones."""
        resume_url = f"{site_url()}/account/orders?pedido={quote(order_number, safe='')}"
        rows = "".join(
            f"""<tr>
              <td style="padding:8px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">{escape(str(i['name']))}</td>
              <td style="padding:8px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#6B6456;text-align:center;">x{i['qty']}</td>
              <td style="padding:8px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;text-align:right;">{Decimal(i['total']):.2f} €</td>
            </tr>"""
            for i in items
        )
        greeting = f"Hola {escape(first_name)}," if first_name else "Hola,"
        inner = f"""
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Tu pedido está pendiente de pago</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            {greeting} empezaste el pedido <strong>{escape(order_number)}</strong> y no llegaste a completar el pago.
            No se ha hecho ningún cargo.
          </p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0;">{rows}</table>
          <p style="font-family:Arial,sans-serif;font-size:16px;font-weight:700;color:#7B1716;">Total: {Decimal(total):.2f} € (IVA incluido)</p>
          <p style="font-family:Arial,sans-serif;font-size:14px;color:#6B6456;line-height:1.6;">
            Si no lo completas en unas 24 horas, cancelaremos el pedido automáticamente.
            Puedes retomarlo desde «Mis pedidos» con el botón «Continuar con el pago».
          </p>
          {_btn(resume_url, "Completar mi pedido")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Si ya no lo quieres, no tienes que hacer nada. Si necesitas ayuda, escríbenos a
            <a href="mailto:pedidos@cremacuadrado.com" style="color:#7B1716;">pedidos@cremacuadrado.com</a>.
          </p>
        """
        text = (
            f"{greeting} tu pedido {order_number} está pendiente de pago ({Decimal(total):.2f} €). "
            f"Si no lo completas en unas 24 horas, lo cancelaremos. Complétalo aquí: {resume_url}"
        )
        return _send(
            to_email,
            f"Tu pedido {order_number} sigue pendiente de pago · CremaCuadrado",
            _wrap_layout(inner),
            text,
            mailbox="pedidos",
        )

    @classmethod
    def send_payment_failed_email(
        cls, to_email: str, order_number: str, customer_name: str, error_message: Optional[str] = None
    ) -> bool:
        """Notify the customer that their payment could not be processed."""
        retry_url = f"{site_url()}/carrito"
        reason_block = ""
        if error_message:
            reason_block = f"""
              <div style="margin:16px 0 0;padding:16px;background-color:#FDECEA;border-radius:6px;border:1px solid #f5c6c0;">
                <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
                  <strong style="color:#7B1716;">Motivo:</strong> {escape(error_message)}
                </p>
              </div>"""
        inner = f"""
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">No hemos podido procesar tu pago</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola <strong>{escape(customer_name)}</strong>, ha habido un problema al cobrar tu pedido <strong>{order_number}</strong>.
            No se ha hecho ningún cargo y tus productos siguen en el carrito.
          </p>
          {reason_block}
          {_btn(retry_url, "Reintentar el pago")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Si el problema persiste, escríbenos a
            <a href="mailto:pedidos@cremacuadrado.com" style="color:#7B1716;">pedidos@cremacuadrado.com</a>.
          </p>
        """
        return _send(
            to_email,
            f"No hemos podido cobrar tu pedido {order_number} · CremaCuadrado",
            _wrap_layout(inner),
            mailbox="pedidos",
        )

    @classmethod
    def send_order_status_update_email(
        cls, to_email: str, order_number: str, customer_name: str, new_status: str,
        is_guest: bool = False,
    ) -> bool:
        status_labels = {
            "processing": "En preparación",
            "shipped": "Enviado",
            "delivered": "Entregado",
            "cancelled": "Cancelado",
            "refunded": "Reembolsado",
        }
        label = status_labels.get(new_status, new_status)
        inner = f"""
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Actualización de tu pedido</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Hola <strong>{escape(customer_name)}</strong>, tu pedido <strong>{order_number}</strong> ha cambiado de estado.</p>
          <div style="margin:24px 0;padding:16px;background:#F4F1E9;border-radius:6px;text-align:center;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#7B1716;">{label}</p>
          </div>
          {"" if is_guest else _btn(site_url() + "/account/orders", "Ver mis pedidos")}
        """
        return _send(to_email, f"Pedido {order_number}: {label} · CremaCuadrado", _wrap_layout(inner), mailbox="pedidos")

    @classmethod
    def send_admin_new_order(
        cls,
        order_number: str,
        customer_name: str,
        customer_email: str,
        total: float,
        items_summary: str,
        shipping_address: dict,
        tracking_number: str | None = None,
    ) -> bool:
        """Notify admin@cremacuadrado.com of a new paid order."""
        addr = {k: escape(str(v)) if v is not None else "" for k, v in shipping_address.items()}
        address_html = (
            f"{addr.get('street', '')} {addr.get('street_2', '') or ''}<br>"
            f"{addr.get('postal_code', '')} {addr.get('city', '')}<br>"
            f"{addr.get('province', '')}, {addr.get('country', 'España')}<br>"
            f"Tel: {addr.get('phone', '')}"
        )
        tracking_html = (
            f"<p style='font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;'>"
            f"<strong>Localizador Correos:</strong> {tracking_number}</p>"
        ) if tracking_number else "<p style='font-family:Arial,sans-serif;font-size:13px;color:#6B6456;'>Envío Correos pendiente de generarse.</p>"

        inner = f"""
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Nuevo pedido: {order_number}</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">
            <strong>{escape(customer_name)}</strong> · {escape(customer_email or "")}
          </p>
          <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;width:140px;">Productos</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{escape(items_summary)}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Total</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:700;color:#7B1716;">{total:.2f} €</td></tr>
            <tr><td style="padding:8px;color:#6B6456;vertical-align:top;">Dirección envío</td>
                <td style="padding:8px;color:#1C1A14;line-height:1.6;">{address_html}</td></tr>
          </table>
          <div style="margin:24px 0 0;">
            {tracking_html}
          </div>
          {_btn(site_url() + "/admin/orders", "Ver en el panel admin")}
        """
        return _send(
            settings.ADMIN_EMAIL,
            f"[CremaCuadrado] Nuevo pedido {order_number} · {total:.2f} €",
            _wrap_layout(inner),
            mailbox="pedidos",
        )

    @classmethod
    def send_admin_status_change(
        cls, order_number: str, new_status: str, tracking_number: str | None = None
    ) -> bool:
        """Notify admin of an automated status change (e.g. Correos update)."""
        status_labels = {
            "processing": "En preparación", "shipped": "Enviado",
            "delivered": "Entregado", "cancelled": "Cancelado",
        }
        label = status_labels.get(new_status, new_status)
        tracking_line = f"<p style='font-family:Arial,sans-serif;font-size:14px;'>Localizador: <strong>{tracking_number}</strong></p>" if tracking_number else ""
        inner = f"""
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:20px;color:#7B1716;">
            Pedido {order_number} → {label}
          </h2>
          {tracking_line}
          {_btn(site_url() + "/admin/orders", "Ver en el panel admin")}
        """
        return _send(
            settings.ADMIN_EMAIL,
            f"[CremaCuadrado] Pedido {order_number} · {label}",
            _wrap_layout(inner),
            mailbox="pedidos",
        )

    @classmethod
    def send_security_notification(cls, to_email: str, first_name: str, event: str) -> bool:
        """Notify the user of a sensitive account change (password change, reset, etc.)."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Aviso de seguridad</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola <strong>{escape(first_name or "")}</strong>, se ha registrado un <strong>{event}</strong> en tu cuenta de CremaCuadrado.
          </p>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Si no has sido tú, escríbenos cuanto antes a
            <a href="mailto:info@cremacuadrado.com" style="color:#7B1716;">info@cremacuadrado.com</a>.
          </p>
        """
        return _send(to_email, f"Aviso de seguridad: {event} · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_pos_lead_confirmation_email(cls, to_email: str, establishment_name: str) -> bool:
        """Confirm receipt of a /para-tiendas B2B lead to the person who submitted it."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Gracias por tu interés, {escape(establishment_name)}</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hemos recibido tu solicitud para convertirte en punto de venta CremaCuadrado. Te escribiremos en un
            plazo máximo de <strong>48 horas</strong> para explicarte las condiciones
            comerciales y, si te interesa, enviarte una muestra gratuita.
          </p>
          {_btn(site_url() + "/para-tiendas", "Ver ventajas de ser punto de venta")}
        """
        return _send(to_email, "Hemos recibido tu solicitud · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_admin_new_pos_lead(
        cls,
        name: str,
        establishment_name: str,
        city: str,
        establishment_type: str,
        email: str,
        phone: str,
    ) -> bool:
        """Notify the B2B team of a new /para-tiendas lead."""
        subject = f"[CremaCuadrado] Nuevo lead punto de venta · {establishment_name}"
        name, establishment_name, city = escape(name), escape(establishment_name), escape(city)
        establishment_type, email, phone = escape(establishment_type), escape(email), escape(phone or "")
        inner = f"""
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Nuevo lead: punto de venta</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">
            Solicitud recibida desde /para-tiendas
          </p>
          <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;width:160px;">Nombre</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{name}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Tienda</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{establishment_name}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Tipo de establecimiento</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{establishment_type}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Ciudad</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{city}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Email</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{email}</td></tr>
            <tr><td style="padding:8px;color:#6B6456;">Teléfono</td>
                <td style="padding:8px;color:#1C1A14;">{phone}</td></tr>
          </table>
        """
        return _send(
            settings.B2B_NOTIFICATION_EMAIL,
            subject,
            _wrap_layout(inner),
            mailbox="info",
        )

    @classmethod
    def send_contact_form_confirmation(cls, to_email: str, name: str) -> bool:
        """Confirm receipt of a /contacto form submission to the sender."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Gracias por escribirnos, {escape(name)}</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hemos recibido tu mensaje y te responderemos a este mismo email.
            Solemos contestar en menos de <strong>24&nbsp;horas</strong>.
          </p>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Mientras tanto, puedes mirar el catálogo.
          </p>
          {_btn(site_url() + "/catalogo", "Ver productos")}
        """
        return _send(to_email, "Hemos recibido tu mensaje · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_admin_contact_form(
        cls,
        name: str,
        email: str,
        message: str,
        accepts_marketing: bool,
    ) -> bool:
        """Notify info@ of a new contact form submission."""
        reply_url = "mailto:" + email
        subject = f"[CremaCuadrado] Nuevo contacto · {name}"
        name, email, message = escape(name), escape(email), escape(message)
        marketing_label = "Sí" if accepts_marketing else "No"
        inner = f"""
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Nuevo mensaje de contacto</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">
            Formulario recibido desde /contacto
          </p>
          <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;width:160px;">Nombre</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{name}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Email</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{email}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Acepta comunicaciones</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{marketing_label}</td></tr>
            <tr><td style="padding:8px;vertical-align:top;color:#6B6456;">Mensaje</td>
                <td style="padding:8px;color:#1C1A14;white-space:pre-wrap;">{message}</td></tr>
          </table>
          {_btn(reply_url, "Responder")}
        """
        return _send(
            settings.SMTP_INFO_FROM_EMAIL,
            subject,
            _wrap_layout(inner),
            mailbox="info",
        )

    @classmethod
    def send_email_verification(cls, to_email: str, first_name: str, token: str, legacy_benefit: bool = False) -> bool:
        """Send email address verification link (legacy_benefit: cliente de la web antigua, ver legacy_customers.py)."""
        verify_url = f"{site_url()}/auth/verify-email?token={token}"
        legacy = ""
        if legacy_benefit:
            legacy = """
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Como ya eras cliente de nuestra web anterior, te espera un <strong>20% de descuento en todos tus pedidos durante un año</strong>.
            Te enviamos tu código personal en cuanto verifiques el email.
          </p>"""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Verifica tu email</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola <strong>{escape(first_name or "")}</strong>, pulsa el botón para verificar tu dirección de email. El enlace caduca en 24 horas.
          </p>{legacy}
          {_btn(verify_url, "Verificar email")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Si no te has registrado en CremaCuadrado, ignora este email.
          </p>
        """
        return _send(to_email, "Verifica tu email · CremaCuadrado", _wrap_layout(inner))


def send_invoice_email(
    to_email: str,
    first_name: str,
    order_number: str,
    invoice_number: str,
    pdf_bytes: bytes,
    corrective: bool = False,
) -> bool:
    """Send an issued invoice PDF (the stored one) as an email attachment."""
    from app.services.company import COMPANY
    kind = "factura rectificativa" if corrective else "factura"
    inner = f"""
      <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">
        Tu {kind}
      </h2>
      <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
        Hola <strong>{escape(first_name or "")}</strong>, adjuntamos la {kind} <strong>{invoice_number}</strong>
        correspondiente al pedido <strong>{order_number}</strong>.
      </p>
      <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
        Guarda el PDF adjunto para tu contabilidad.
      </p>
      <div style="background:#EDE9DF;border-radius:8px;padding:16px 20px;margin:20px 0;">
        <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
          <strong>{COMPANY["name"]}</strong> · NIF {COMPANY["nif"]}<br>
          {COMPANY["address"]} · {COMPANY["postal_code"]} {COMPANY["city"]}<br>
          {COMPANY["email"]} · {COMPANY["phone"]}
        </p>
      </div>
      <p style="font-family:Arial,sans-serif;font-size:13px;color:#6B6456;margin-top:16px;">
        Si tienes alguna duda sobre la factura, escríbenos a
        <a href="mailto:{COMPANY["email"]}" style="color:#7B1716;">{COMPANY["email"]}</a>.
      </p>
    """
    return _send_with_attachment(
        to_email=to_email,
        subject=f"{kind.capitalize()} {invoice_number} · CremaCuadrado",
        html=_wrap_layout(inner),
        attachment_bytes=pdf_bytes,
        attachment_filename=f"Factura_{invoice_number}.pdf",
        mailbox="pedidos",
    )
