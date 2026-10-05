"""
Datos fiscales del emisor (CREMACUADRADO SL).

Única fuente para facturas y emails. Cada factura guarda una copia (seller_snapshot)
en el momento de emitirse, así que cambiar algo aquí no altera facturas ya emitidas.
"""

COMPANY = {
    "name": "CREMACUADRADO SL",
    "nif": "B56673700",
    "address": "Camino del Arca 18",
    "postal_code": "13005",
    "city": "Ciudad Real",
    "province": "Ciudad Real",
    "country": "España",
    "email": "admin@cremacuadrado.com",
    # ⚠️ Pendiente de confirmar: la política de privacidad usa 623 294 886.
    "phone": "623 286 353",
    "registry": "Registro Mercantil de Ciudad Real, Tomo 725, Hoja CR-33764",
}


def seller_snapshot() -> dict:
    """Copia inmutable de los datos del emisor para guardar en la factura."""
    return dict(COMPANY)
