"""Email service - MVP: logs to console instead of sending real emails."""
from typing import Optional
from app.config import settings


class EmailService:
    """
    Email service for sending transactional emails.
    
    MVP Implementation: Logs emails to console instead of actually sending them.
    In production, integrate with SendGrid, SES, or similar service.
    """
    
    @staticmethod
    def send_email(
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Send an email.
        
        MVP: Logs to console.
        """
        if not settings.EMAIL_ENABLED:
            print("=" * 60)
            print(f"📧 EMAIL (not sent - EMAIL_ENABLED=False)")
            print(f"To: {to_email}")
            print(f"From: {settings.EMAIL_FROM}")
            print(f"Subject: {subject}")
            print("-" * 60)
            print(html_content[:500] + "..." if len(html_content) > 500 else html_content)
            print("=" * 60)
            return True
        
        # TODO: Implement real email sending here
        # Example with SendGrid:
        # import sendgrid
        # sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        # ...
        
        return True
    
    @classmethod
    def send_welcome_email(cls, to_email: str, first_name: str) -> bool:
        """Send welcome email after registration."""
        subject = "¡Bienvenido a Cremacuadrado!"
        html_content = f"""
        <h1>¡Hola {first_name}!</h1>
        <p>Gracias por registrarte en Cremacuadrado.</p>
        <p>Ya puedes disfrutar de nuestras deliciosas cremas de pistacho artesanales.</p>
        <p><a href="https://cremacuadrado.com/tienda">Visitar la tienda</a></p>
        <br>
        <p>Un saludo,<br>El equipo de Cremacuadrado</p>
        """
        return cls.send_email(to_email, subject, html_content)
    
    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_token: str) -> bool:
        """Send password reset email."""
        subject = "Restablecer contraseña - Cremacuadrado"
        reset_url = f"https://cremacuadrado.com/auth/reset-password?token={reset_token}"
        html_content = f"""
        <h1>Restablecer contraseña</h1>
        <p>Has solicitado restablecer tu contraseña.</p>
        <p>Haz clic en el siguiente enlace para crear una nueva contraseña:</p>
        <p><a href="{reset_url}">Restablecer contraseña</a></p>
        <p>Este enlace expirará en 1 hora.</p>
        <p>Si no has solicitado esto, puedes ignorar este email.</p>
        <br>
        <p>Un saludo,<br>El equipo de Cremacuadrado</p>
        """
        return cls.send_email(to_email, subject, html_content)
    
    @classmethod
    def send_order_confirmation_email(
        cls,
        to_email: str,
        order_number: str,
        customer_name: str,
        total: str,
        items_html: str
    ) -> bool:
        """Send order confirmation email."""
        subject = f"Confirmación de pedido #{order_number} - Cremacuadrado"
        html_content = f"""
        <h1>¡Gracias por tu pedido, {customer_name}!</h1>
        <p>Hemos recibido tu pedido y lo estamos preparando.</p>
        
        <h2>Resumen del pedido #{order_number}</h2>
        {items_html}
        <p><strong>Total: {total}</strong></p>
        
        <p>Te avisaremos cuando enviemos tu pedido.</p>
        <p><a href="https://cremacuadrado.com/mi-cuenta/pedidos/{order_number}">Ver detalles del pedido</a></p>
        <br>
        <p>Un saludo,<br>El equipo de Cremacuadrado</p>
        """
        return cls.send_email(to_email, subject, html_content)
    
    @classmethod
    def send_order_shipped_email(
        cls,
        to_email: str,
        order_number: str,
        customer_name: str,
        tracking_number: Optional[str] = None
    ) -> bool:
        """Send order shipped notification email."""
        subject = f"Tu pedido #{order_number} ha sido enviado - Cremacuadrado"
        
        tracking_html = ""
        if tracking_number:
            tracking_html = f"""
            <p><strong>Número de seguimiento:</strong> {tracking_number}</p>
            <p><a href="https://www.correos.es/es/es/herramientas/localizador/envios/{tracking_number}">
                Seguir envío en Correos
            </a></p>
            """
        
        html_content = f"""
        <h1>¡Tu pedido está en camino, {customer_name}!</h1>
        <p>Tu pedido #{order_number} ha sido enviado.</p>
        {tracking_html}
        <p>El tiempo de entrega estimado es de 48-72 horas.</p>
        <br>
        <p>Un saludo,<br>El equipo de Cremacuadrado</p>
        """
        return cls.send_email(to_email, subject, html_content)
    
    @classmethod
    def send_order_status_update_email(
        cls,
        to_email: str,
        order_number: str,
        customer_name: str,
        new_status: str
    ) -> bool:
        """Send order status update email."""
        status_messages = {
            "processing": "Estamos preparando tu pedido",
            "shipped": "Tu pedido ha sido enviado",
            "delivered": "Tu pedido ha sido entregado",
            "cancelled": "Tu pedido ha sido cancelado",
            "refunded": "Tu pedido ha sido reembolsado",
        }
        
        message = status_messages.get(new_status, f"El estado ha cambiado a: {new_status}")
        subject = f"Actualización de pedido #{order_number} - Cremacuadrado"
        
        html_content = f"""
        <h1>Actualización de tu pedido</h1>
        <p>Hola {customer_name},</p>
        <p>{message}</p>
        <p>Número de pedido: <strong>{order_number}</strong></p>
        <p><a href="https://cremacuadrado.com/mi-cuenta/pedidos/{order_number}">Ver detalles del pedido</a></p>
        <br>
        <p>Un saludo,<br>El equipo de Cremacuadrado</p>
        """
        return cls.send_email(to_email, subject, html_content)
