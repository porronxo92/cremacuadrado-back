"""
Email service — SMTP with inline-CSS HTML templates.
Set EMAIL_ENABLED=True in .env and configure SMTP_* variables to send real emails.
"""
import logging
import smtplib
from datetime import datetime
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import settings

logger = logging.getLogger("cremacuadrado.email")


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def _send(to_email: str, subject: str, html: str, text: str = "") -> bool:
    sender = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"

    if not settings.EMAIL_ENABLED:
        logger.info("Email suppressed (EMAIL_ENABLED=False) to=%s subject=%r", to_email, subject)
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email
        if text:
            msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.EMAIL_FROM, [to_email], msg.as_string())

        logger.info("Email sent to=%s subject=%r", to_email, subject)
        return True
    except Exception:
        logger.error("Email failed to=%s subject=%r", to_email, subject, exc_info=True)
        return False


def _send_with_attachment(
    to_email: str,
    subject: str,
    html: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    text: str = "",
) -> bool:
    sender = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"

    if not settings.EMAIL_ENABLED:
        logger.info(
            "Email suppressed (EMAIL_ENABLED=False) to=%s subject=%r attachment=%s",
            to_email, subject, attachment_filename,
        )
        return True

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email

        body = MIMEMultipart("alternative")
        if text:
            body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body)

        pdf_part = MIMEApplication(attachment_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(pdf_part)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.EMAIL_FROM, [to_email], msg.as_string())

        logger.info("Email with attachment sent to=%s subject=%r", to_email, subject)
        return True
    except Exception:
        logger.error("Email with attachment failed to=%s subject=%r", to_email, subject, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Shared HTML layout
# ---------------------------------------------------------------------------

def _wrap_layout(inner_html: str) -> str:
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
                <a href="{settings.SITE_URL}/privacidad" style="color:#6B6456;text-decoration:none;">Privacidad</a>
                &nbsp;·&nbsp;
                <a href="{settings.SITE_URL}/condiciones-venta" style="color:#6B6456;text-decoration:none;">Condiciones de venta</a>
                &nbsp;·&nbsp;
                <a href="{settings.SITE_URL}/devoluciones" style="color:#6B6456;text-decoration:none;">Devoluciones</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _btn(url: str, label: str) -> str:
    return f"""<p style="text-align:center;margin:32px 0 0;">
  <a href="{url}"
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
    ):
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


def send_order_confirmation(data: OrderEmailData) -> bool:
    order_url = f"{settings.SITE_URL}/account/orders"
    date_str = data.order_date.strftime("%d/%m/%Y %H:%M")

    # Items rows
    items_rows = ""
    for item in data.items:
        items_rows += f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #F0EBE1;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">
            {item['name']}
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
        coupon_label = f" ({data.coupon_code})" if data.coupon_code else ""
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
    addr_html = "<br>".join(line for line in addr_lines if line)

    # Tracking (Correos) — present only when a localizador was generated
    tracking_block = ""
    info_text = (
        "Estamos preparando tu pedido. Recibirás otro email en cuanto lo enviemos con el "
        "número de seguimiento de Correos. El plazo de entrega habitual es de "
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
            "Estamos preparando tu pedido. Puedes seguir su estado con el número de "
            "seguimiento de arriba. El plazo de entrega habitual es de "
            "<strong>2–4 días hábiles</strong>."
        )

    # Notes
    notes_block = ""
    if data.customer_notes:
        notes_block = f"""
        <div style="margin-top:24px;padding:16px;background-color:#F4F1E9;border-radius:6px;border-left:3px solid #E6C15A;">
          <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#6B6456;">Notas del pedido</p>
          <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">{data.customer_notes}</p>
        </div>"""

    inner = f"""
      <!-- Greeting -->
      <h2 style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:22px;font-weight:700;color:#7B1716;">
        ¡Gracias por tu pedido, {data.customer_name}!
      </h2>
      <p style="margin:0 0 28px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;line-height:1.5;">
        Hemos recibido tu pedido y lo estamos preparando con todo el cuidado que se merece.
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
        <p style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:14px;font-weight:600;color:#1C1A14;">¿Qué pasa ahora?</p>
        <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;line-height:1.6;">
          {info_text}
        </p>
      </div>

      {_btn(order_url, "Ver mis pedidos")}
    """

    html = _wrap_layout(inner)
    subject = f"Pedido {data.order_number} confirmado · CremaCuadrado"

    text = (
        f"Hola {data.customer_name}, tu pedido {data.order_number} ha sido confirmado.\n"
        f"Total: {data.total:.2f} €\n"
        f"Puedes ver los detalles en: {order_url}"
    )

    return _send(data.to_email, subject, html, text)


# ---------------------------------------------------------------------------
# Legacy method (used in non-webhook paths) — delegates to send_order_confirmation
# ---------------------------------------------------------------------------

class EmailService:

    @staticmethod
    def send_email(to_email: str, subject: str, html_content: str, text_content: str = "") -> bool:
        return _send(to_email, subject, html_content, text_content)

    @classmethod
    def send_welcome_email(cls, to_email: str, first_name: str) -> bool:
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">¡Bienvenido, {first_name}!</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Gracias por registrarte en CremaCuadrado. Ya puedes disfrutar de nuestras cremas de pistacho manchego artesanales.
          </p>
          {_btn(settings.SITE_URL + "/tienda", "Ver productos")}
        """
        return _send(to_email, "¡Bienvenido a CremaCuadrado!", _wrap_layout(inner))

    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_token: str) -> bool:
        reset_url = f"{settings.SITE_URL}/auth/reset-password?token={reset_token}"
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Restablecer contraseña</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Has solicitado restablecer tu contraseña. Haz clic en el botón y crea una nueva. El enlace caduca en 1 hora.
          </p>
          {_btn(reset_url, "Restablecer contraseña")}
          <p style="margin-top:24px;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
            Si no has solicitado esto, ignora este email.
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
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">¡Gracias por tu pedido, {customer_name}!</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Pedido <strong>{order_number}</strong></p>
          <div style="font-family:Arial,sans-serif;font-size:14px;color:#1C1A14;">{items_html}</div>
          <p style="margin-top:16px;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#7B1716;">Total: {total}</p>
          {_btn(settings.SITE_URL + "/account/orders", "Ver mi pedido")}
        """
        return _send(
            to_email,
            f"Pedido {order_number} confirmado · CremaCuadrado",
            _wrap_layout(inner),
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
          <h2 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">¡Tu pedido está en camino, {customer_name}!</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Pedido <strong>{order_number}</strong> · Entrega estimada: 2–4 días hábiles</p>
          {tracking_block}
        """
        return _send(to_email, f"Tu pedido {order_number} ha sido enviado · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_order_status_update_email(
        cls, to_email: str, order_number: str, customer_name: str, new_status: str
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
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">Hola <strong>{customer_name}</strong>, tu pedido <strong>{order_number}</strong> ha cambiado de estado.</p>
          <div style="margin:24px 0;padding:16px;background:#F4F1E9;border-radius:6px;text-align:center;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#7B1716;">{label}</p>
          </div>
          {_btn(settings.SITE_URL + "/account/orders", "Ver mis pedidos")}
        """
        return _send(to_email, f"Pedido {order_number} — {label} · CremaCuadrado", _wrap_layout(inner))

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
        addr = shipping_address
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
          <h2 style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">🛒 Nuevo pedido: {order_number}</h2>
          <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:15px;color:#6B6456;">
            <strong>{customer_name}</strong> — {customer_email}
          </p>
          <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;width:140px;">Productos</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#1C1A14;">{items_summary}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#6B6456;">Total</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:700;color:#7B1716;">{total:.2f} €</td></tr>
            <tr><td style="padding:8px;color:#6B6456;vertical-align:top;">Dirección envío</td>
                <td style="padding:8px;color:#1C1A14;line-height:1.6;">{address_html}</td></tr>
          </table>
          <div style="margin:24px 0 0;">
            {tracking_html}
          </div>
          {_btn(settings.SITE_URL + "/admin/orders", "Ver en el panel admin")}
        """
        return _send(
            settings.ADMIN_EMAIL,
            f"[CremaCuadrado] Nuevo pedido {order_number} — {total:.2f} €",
            _wrap_layout(inner),
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
          {_btn(settings.SITE_URL + "/admin/orders", "Ver en el panel admin")}
        """
        return _send(
            settings.ADMIN_EMAIL,
            f"[CremaCuadrado] Pedido {order_number} — {label}",
            _wrap_layout(inner),
        )

    @classmethod
    def send_security_notification(cls, to_email: str, first_name: str, event: str) -> bool:
        """Notify the user of a sensitive account change (password change, reset, etc.)."""
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Aviso de seguridad</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola <strong>{first_name}</strong>, te informamos de que se ha realizado un <strong>{event}</strong> en tu cuenta CremaCuadrado.
          </p>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Si no has realizado este cambio, ponte en contacto con nosotros de inmediato en
            <a href="mailto:info@cremacuadrado.com" style="color:#7B1716;">info@cremacuadrado.com</a>.
          </p>
        """
        return _send(to_email, f"Aviso de seguridad: {event} · CremaCuadrado", _wrap_layout(inner))

    @classmethod
    def send_email_verification(cls, to_email: str, first_name: str, token: str) -> bool:
        """Send email address verification link."""
        verify_url = f"{settings.SITE_URL}/auth/verify-email?token={token}"
        inner = f"""
          <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">Verifica tu email</h2>
          <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
            Hola <strong>{first_name}</strong>, haz clic en el botón para verificar tu dirección de email. El enlace caduca en 24 horas.
          </p>
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
    pdf_bytes: bytes,
) -> bool:
    """Send invoice PDF as email attachment to the customer."""
    from app.services.invoice import _invoice_number
    invoice_number = _invoice_number(order_number)
    pdf_filename = f"Factura_{invoice_number}.pdf"

    inner = f"""
      <h2 style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:22px;color:#7B1716;">
        Tu factura está lista
      </h2>
      <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
        Hola <strong>{first_name}</strong>, adjuntamos la factura correspondiente al pedido
        <strong>{order_number}</strong>.
      </p>
      <p style="font-family:Arial,sans-serif;font-size:15px;color:#1C1A14;line-height:1.6;">
        El número de factura es <strong>{invoice_number}</strong>.
        Puedes guardar el PDF adjunto para tus registros contables.
      </p>
      <div style="background:#EDE9DF;border-radius:8px;padding:16px 20px;margin:20px 0;">
        <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#6B6456;">
          <strong>Cremacuadrado SL</strong> · CIF B56673700<br>
          Camino del arca 18 · 13005 Ciudad Real<br>
          Admin@cremacuadrado.com · 623 286 353
        </p>
      </div>
      <p style="font-family:Arial,sans-serif;font-size:13px;color:#6B6456;margin-top:16px;">
        Si tienes alguna duda sobre la factura, escríbenos a
        <a href="mailto:Admin@cremacuadrado.com" style="color:#7B1716;">Admin@cremacuadrado.com</a>.
      </p>
    """
    return _send_with_attachment(
        to_email=to_email,
        subject=f"Factura {invoice_number} · CremaCuadrado",
        html=_wrap_layout(inner),
        attachment_bytes=pdf_bytes,
        attachment_filename=pdf_filename,
    )
