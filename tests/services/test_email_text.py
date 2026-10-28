"""Todos los emails salen con parte text/plain (los filtros de spam penalizan los solo-HTML)."""
from app.services import email as email_service
from app.services.email import EmailService, html_to_text


def test_html_to_text_keeps_links_and_drops_styles():
    html = ('<html><head><style>p{color:red}</style></head><body><h1>Hola&nbsp;Ana</h1>'
            '<p>Tu pedido <strong>CC-1</strong> está en camino.</p>'
            '<p><a href="https://cremacuadrado.com/account/orders">Ver mis pedidos</a></p>'
            '<ul><li>Pura 200g</li></ul></body></html>')
    text = html_to_text(html)
    assert "color:red" not in text and "<" not in text
    assert "Hola Ana" in text
    assert "Tu pedido CC-1 está en camino." in text
    assert "Ver mis pedidos (https://cremacuadrado.com/account/orders)" in text
    assert "- Pura 200g" in text


def test_resend_payload_always_has_text(monkeypatch):
    import resend
    sent = {}
    monkeypatch.setattr(email_service.settings, "EMAIL_ENABLED", True)
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(resend.Emails, "send", staticmethod(lambda params: sent.update(params)))
    assert EmailService.send_order_status_update_email(
        to_email="a@example.com", order_number="CC-1", customer_name="Ana", new_status="refunded", is_guest=True)
    assert sent["html"] and sent["text"]
    assert "Reembolsado" in sent["text"]
