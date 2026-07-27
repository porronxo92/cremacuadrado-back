#!/usr/bin/env python
"""
Script de prueba para la integración con la API de Correos España.

Simula el flujo completo post-pago:
  1. Autenticación OAuth2 (CorreosID)
  2. Prerregistro del envío → obtiene shipmentCode / packageCode
  3. Generación de etiqueta PDF → guarda en /tmp/etiqueta_test.pdf
  4. Consulta de tracking (trackpub)

Uso:
  cd backend
  python scripts/test_correos.py                     # modo MOCK (sin llamadas reales)
  python scripts/test_correos.py --real              # llamadas reales a API de PRE

Requiere que el .env esté en backend/.env
"""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# ── Añadir el directorio backend al path ─────────────────────────────
BACKEND_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

# ── Cargar .env manualmente (sin depender de la app arrancada) ────────
def load_env(env_path: Path):
    if not env_path.exists():
        print(f"[ERROR] No se encontró el .env en {env_path}")
        sys.exit(1)
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

load_env(BACKEND_DIR / ".env")

# ── Importar settings y módulos Correos ─────────────────────────────
from app.config import settings  # noqa: E402


# ── Objeto pedido simulado ───────────────────────────────────────────
class MockOrder:
    """Simula un pedido completado de CremaCuadrado."""
    id = 9999
    order_number = "CC-TEST-9999"
    customer_email = "cliente@test.com"
    shipping_status = "pending"
    tracking_number = None

    shipping_address = {
        "first_name": "Juan",
        "last_name": "García",
        "street": "Calle Gran Vía",
        "street_2": "Piso 3, Puerta B",
        "city": "Madrid",
        "province": "Madrid",
        "postal_code": "28013",
        "country": "ESP",
        "phone": "600123456",
    }


class MockVariant:
    weight_grams = 250


class MockItem:
    quantity = 2
    variant = MockVariant()


# ─────────────────────────────────────────────────────────────────────

def separator(title: str):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print('═'*60)


def print_env_summary():
    separator("CONFIGURACIÓN CORREOS (.env)")
    fields = [
        ("CORREOS_ENABLED",          settings.CORREOS_ENABLED),
        ("CORREOS_API_BASE",         settings.CORREOS_API_BASE),
        ("CORREOS_OAUTH_URL",        settings.CORREOS_OAUTH_URL),
        ("CORREOS_CLIENT_ID",        settings.CORREOS_CLIENT_ID[:8] + "…" if settings.CORREOS_CLIENT_ID else "⚠ VACÍO"),
        ("CORREOS_CLIENT_SECRET",    "✓ configurado" if settings.CORREOS_CLIENT_SECRET else "⚠ VACÍO"),
        ("CLIENT_ID_API",            settings.CLIENT_ID_API[:8] + "…" if settings.CLIENT_ID_API else "⚠ VACÍO"),
        ("CLIENT_SECRET_API",        "✓ configurado" if settings.CLIENT_SECRET_API else "⚠ VACÍO"),
        ("CORREOS_NUM_CONTRATO",     settings.CORREOS_NUM_CONTRATO or "⚠ VACÍO"),
        ("CORREOS_NUM_SOLICITANTE",  settings.CORREOS_NUM_SOLICITANTE or "⚠ VACÍO"),
        ("CODIGO_ETIQUETADOR",       settings.CODIGO_ETIQUETADOR or "⚠ VACÍO"),
        ("CORREOS_SERVICE_CODE",     settings.CORREOS_SERVICE_CODE),
        ("CORREOS_SENDER_NAME",      settings.CORREOS_SENDER_NAME or "⚠ VACÍO"),
        ("CORREOS_SENDER_ADDRESS",   settings.CORREOS_SENDER_ADDRESS or "⚠ VACÍO"),
        ("CORREOS_SENDER_CITY",      settings.CORREOS_SENDER_CITY or "⚠ VACÍO"),
        ("CORREOS_SENDER_POSTAL_CODE", settings.CORREOS_SENDER_POSTAL_CODE or "⚠ VACÍO"),
    ]
    for name, value in fields:
        icon = "✓" if "⚠" not in str(value) else "⚠"
        print(f"  {icon}  {name:<30} = {value}")


def step_auth():
    separator("PASO 1 — Autenticación OAuth2 (CorreosID)")
    from app.services.correos.auth import get_access_token

    if not settings.CORREOS_ENABLED:
        print("  → Modo MOCK: se omite llamada real a CorreosID")
        token = get_access_token()
        print(f"  ✓ Token mock: {token}")
        return token

    print(f"  → Llamando a {settings.CORREOS_OAUTH_URL} ...")
    try:
        token = get_access_token()
        print(f"  ✓ Token obtenido: {token[:20]}…")
        return token
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None


def step_preregister(order: MockOrder, weight_grams: int) -> dict | None:
    separator("PASO 2 — Prerregistro del envío")
    from app.services.correos.preregister import build_payload, preregister_shipment

    # Mostrar el payload que se enviará
    payload = build_payload(order, weight_grams)
    print("  Payload que se enviará a Correos:")
    print(json.dumps(payload, indent=4, ensure_ascii=False))

    if not settings.CORREOS_ENABLED:
        print("\n  → Modo MOCK: se omite llamada real")
    else:
        print(f"\n  → Llamando a {settings.CORREOS_API_BASE}/admissions/preregister/api/v1/delivery ...")

    try:
        result = preregister_shipment(order, weight_grams)
        print("\n  ✓ Resultado:")
        print(f"    shipment_code : {result.get('shipment_code')}")
        print(f"    package_code  : {result.get('package_code')}")
        if settings.CORREOS_ENABLED:
            print(f"    Respuesta raw : {json.dumps(result.get('response', {}), indent=4, ensure_ascii=False)}")
        return result
    except Exception as e:
        print(f"  ✗ Error en prerregistro: {e}")
        return None


def step_label(package_code: str):
    separator("PASO 3 — Generación de etiqueta PDF")
    from app.services.correos.labels import get_label_pdf

    if not settings.CORREOS_ENABLED:
        print("  → Modo MOCK: se genera PDF de prueba")
    else:
        print(f"  → Llamando a {settings.CORREOS_API_BASE}/support/labels/api/v1/labels/print ...")
        print(f"    packageCode: {package_code}")

    try:
        pdf_bytes = get_label_pdf(package_code)
        output_path = Path("/tmp") / f"etiqueta_{package_code}.pdf"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(pdf_bytes)
        print(f"  ✓ PDF generado: {len(pdf_bytes)} bytes")
        print(f"  ✓ Guardado en: {output_path}")
    except Exception as e:
        print(f"  ✗ Error al generar etiqueta: {e}")


def step_tracking(package_code: str):
    separator("PASO 4 — Consulta de tracking (trackpub)")
    from app.services.correos.tracking import get_tracking_events

    if not settings.CORREOS_ENABLED:
        print("  → Modo MOCK: se devuelven eventos simulados")
    else:
        print(f"  → Llamando a {settings.CORREOS_API_BASE}/support/trackpub/api/v2/search/{package_code} ...")
        print(f"    Headers: Authorization + client_id + client_secret")

    try:
        data = get_tracking_events(package_code)
        events = data.get("events", [])
        print(f"  ✓ {len(events)} evento(s) recibidos:")
        for ev in events:
            ts = f"{ev.get('eventDate', '')} {ev.get('eventHours', '')}".strip()
            print(f"    [{ts}] {ev.get('eventCode','')} — {ev.get('expandedText') or ev.get('summaryText','')}")
    except Exception as e:
        print(f"  ✗ Error al consultar tracking: {e}")


def step_pickup():
    separator("PASO 5 — Solicitud de recogida (opcional)")
    from app.services.correos.pickups import request_pickup
    from datetime import date, timedelta

    tomorrow = date.today() + timedelta(days=1)

    if not settings.CORREOS_ENABLED:
        print("  → Modo MOCK: se omite llamada real")
    else:
        print(f"  → Llamando a {settings.CORREOS_API_BASE}/logistics/requests/api/v1/requests ...")
        print(f"    Fecha recogida: {tomorrow.isoformat()}")

    try:
        result = request_pickup(
            pickup_date=tomorrow,
            estimated_shipments=1,
            observations="Prueba integración CremaCuadrado",
        )
        print(f"  ✓ Recogida solicitada: {result.get('codRequests', 'mock')}")
        if settings.CORREOS_ENABLED:
            print(f"    Respuesta: {json.dumps(result, indent=4, ensure_ascii=False)}")
    except Exception as e:
        print(f"  ✗ Error al solicitar recogida: {e}")


def main():
    parser = argparse.ArgumentParser(description="Test integración Correos España")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Activa CORREOS_ENABLED=True para llamadas reales a la API de PRE",
    )
    parser.add_argument(
        "--skip-pickup",
        action="store_true",
        help="Omite el paso de solicitud de recogida",
    )
    args = parser.parse_args()

    if args.real:
        # Override para este script sin tocar el .env
        settings.__dict__["CORREOS_ENABLED"] = True
        print("\n  ⚠ MODO REAL — se harán llamadas a la API de Correos PRE")
    else:
        settings.__dict__["CORREOS_ENABLED"] = False
        print("\n  ℹ MODO MOCK — sin llamadas reales a Correos")

    print(f"\n  Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Mostrar configuración cargada
    print_env_summary()

    # Pedido simulado
    order = MockOrder()
    weight_grams = sum(
        item.variant.weight_grams * item.quantity
        for item in [MockItem()]
    )
    print(f"\n  Pedido simulado: {order.order_number}")
    print(f"  Destinatario:   {order.shipping_address['first_name']} {order.shipping_address['last_name']}")
    print(f"  Dirección:      {order.shipping_address['street']}, {order.shipping_address['city']}")
    print(f"  Código postal:  {order.shipping_address['postal_code']}")
    print(f"  Peso calculado: {weight_grams}g")

    # Paso 1: Auth
    token = step_auth()
    if settings.CORREOS_ENABLED and not token:
        print("\n  ✗ No se pudo obtener token. Revisa CLIENT_ID_API y CLIENT_SECRET_API.")
        sys.exit(1)

    # Paso 2: Prerregistro
    result = step_preregister(order, weight_grams)
    if not result:
        print("\n  ✗ Prerregistro fallido. Abortando.")
        sys.exit(1)

    package_code = result.get("package_code") or result.get("shipment_code")

    # Paso 3: Etiqueta
    step_label(package_code)

    # Paso 4: Tracking
    step_tracking(package_code)

    # Paso 5: Recogida (opcional)
    if not args.skip_pickup:
        step_pickup()

    separator("RESULTADO FINAL")
    print(f"  ✓ shipment_code : {result.get('shipment_code')}")
    print(f"  ✓ package_code  : {result.get('package_code')}")
    print(f"  ✓ tracking_url  : https://www.correos.es/es/es/herramientas/localizador/envios/detalle?tracking-number={package_code}")
    print(f"\n  ✓ Test completado {'(REAL)' if settings.CORREOS_ENABLED else '(MOCK)'}\n")


if __name__ == "__main__":
    main()
