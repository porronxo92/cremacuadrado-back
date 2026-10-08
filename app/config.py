"""
Application configuration using Pydantic Settings.
Loads from environment variables or .env file.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Application
    APP_NAME: str = "Cremacuadrado API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False  # Never True in production

    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cremacuadrado"

    # JWT Authentication
    SECRET_KEY: str  # Required — set via .env, never hardcode
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS — override via env var in production
    # e.g. CORS_ORIGINS=["https://cremacuadrado-front.vercel.app"]
    CORS_ORIGINS: list[str] = [
        "https://cremacuadrado-front.vercel.app",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    # Trusted hosts — set to your domain(s) in production to prevent Host header injection
    # e.g. ALLOWED_HOSTS=["cremacuadrado-back.vercel.app"]
    ALLOWED_HOSTS: list[str] = ["*"]
    
    # Shipping (MVP: fixed price)
    SHIPPING_COST: float = 4.95
    FREE_SHIPPING_THRESHOLD: float = 48.0
    
    # Tax — IVA reducido de alimentación (10 %). El envío, como prestación accesoria,
    # tributa al mismo tipo que el producto. El tipo aplicado se guarda en cada
    # pedido (orders.tax_rate) y en cada factura, así que cambiarlo aquí no altera
    # facturas ya emitidas.
    TAX_RATE: float = 0.10
    
    # Legal — versión vigente de los textos. Se guarda con cada aceptación /
    # consentimiento; súbela cuando cambie el texto publicado en el frontend.
    TERMS_VERSION: str = "2026-10b"    # condiciones generales de venta
    PRIVACY_VERSION: str = "2026-10"   # política de privacidad

    # Envíos: solo península (las CGV lo indican). Prefijos de CP excluidos:
    # 07 Baleares, 35/38 Canarias, 51 Ceuta, 52 Melilla.
    SHIPPING_EXCLUDED_POSTCODE_PREFIXES: list[str] = ["07", "35", "38", "51", "52"]
    SHIPPING_ALLOWED_COUNTRIES: list[str] = ["ES", "España", "Espana", "Spain"]

    # Un carrito de usuario sin actividad durante más de estas horas deja de estar
    # "vigente": al volver a iniciar sesión se vacía en vez de recuperarse.
    USER_CART_TTL_HOURS: int = 48

    # Conservación de datos (días) — ver app/services/retention.py
    RETENTION_GUEST_CART_DAYS: int = 30
    RETENTION_UNCONFIRMED_LEAD_DAYS: int = 30       # newsletter sin confirmar (doble opt-in)
    RETENTION_LEAD_DAYS: int = 730                  # leads B2B / newsletter sin conversión
    RETENTION_CONTACT_DAYS: int = 365
    RETENTION_WEBHOOK_EVENT_DAYS: int = 365
    RETENTION_AUDIT_LOG_DAYS: int = 730

    # Vercel Cron — Vercel envía "Authorization: Bearer <CRON_SECRET>"
    CRON_SECRET: str = ""

    # Pagination
    DEFAULT_PAGE_SIZE: int = 12
    MAX_PAGE_SIZE: int = 100
    
    # Email — set EMAIL_ENABLED=True to send real emails.
    # Two mailboxes route outgoing mail by category (see app/services/email.py):
    #   Pedidos@cremacuadrado.com — confirmación de pedido, fallo de pago, envío, factura
    #   Info@cremacuadrado.com    — bienvenida, verificación, newsletter, contacto, B2B, y todo lo demás
    EMAIL_ENABLED: bool = False
    SITE_URL: str = "http://localhost:4200"

    # Resend API key — when set, all emails are sent via Resend instead of SMTP.
    # Required in production (Vercel/AWS blocks SMTP from hosting providers).
    # Get your key at resend.com → API Keys. Domain must be verified in Resend dashboard.
    RESEND_API_KEY: str = ""

    # Pedidos@cremacuadrado.com — sin credenciales propias todavía, cae en Info@ (ver app/services/email.py)
    SMTP_PEDIDOS_HOST: str = "smtp.titan.email"
    SMTP_PEDIDOS_PORT: int = 587
    SMTP_PEDIDOS_USER: str = ""
    SMTP_PEDIDOS_PASSWORD: str = ""
    SMTP_PEDIDOS_FROM_EMAIL: str = "pedidos@cremacuadrado.com"
    SMTP_PEDIDOS_FROM_NAME: str = "CremaCuadrado Pedidos"

    # Info@cremacuadrado.com
    SMTP_INFO_HOST: str = "smtp.titan.email"
    SMTP_INFO_PORT: int = 587
    SMTP_INFO_USER: str = ""
    SMTP_INFO_PASSWORD: str = ""
    SMTP_INFO_FROM_EMAIL: str = "info@cremacuadrado.com"
    SMTP_INFO_FROM_NAME: str = "CremaCuadrado"
    
    # Admin
    ADMIN_EMAIL: str = "admin@cremacuadrado.com"
    ADMIN_PASSWORD: str  # Required — set via .env, never hardcode

    # B2B — where /para-tiendas and /para-restaurantes lead notifications land
    B2B_NOTIFICATION_EMAIL: str = "b2b@cremacuadrado.com"

    # Base URL for legacy /static/ image paths (leave empty in production — use BLOB_BASE_URL instead)
    BASE_URL: str = ""

    # Public base URL for Vercel Blob images (the /images/... pathnames stored in the DB)
    # e.g. https://r2azgdghbvayayn4.public.blob.vercel-storage.com
    BLOB_BASE_URL: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_CURRENCY: str = "eur"

    # Google OAuth — set GOOGLE_CLIENT_ID in .env with your OAuth 2.0 client ID
    # Get it at: console.cloud.google.com → APIs & Services → Credentials
    GOOGLE_CLIENT_ID: str = ""

    # Ghost order cleanup — cancel pending_payment orders older than this (minutes)
    PENDING_ORDER_EXPIRE_MINUTES: int = 30

    # Vercel Blob — public store for product/blog images
    BLOB_PUBLIC_READ_WRITE_TOKEN: str = ""

    # Vercel Blob — PRIVATE store "cremacuadrado-invoices" (facturas en PDF).
    # Nunca se exponen URLs: el backend descarga el PDF con el token y lo sirve.
    BLOB_INVOICE_READ_WRITE_TOKEN: str = ""
    BLOB_INVOICE_STORE_ID: str = ""  # opcional si el token ya incluye el store id
    BLOB_INVOICE_WEBHOOK_PUBLIC_KEY: str = ""  # reservado (webhooks del store), sin uso

    # Security headers
    SECURE_HEADERS: bool = True  # Set False only for local dev if needed

    # Correos España — set CORREOS_ENABLED=True with a signed contract to call the real API.
    # While False, shipment creation runs in mock mode (returns a fake localizador).
    CORREOS_ENABLED: bool = False
    # CorreosID OAuth2 credentials (portal identidad.correos.es)
    CORREOS_CLIENT_ID: str = ""  # "Cuenta de Cliente de Sistemas" (e.g. W81468585AJDD)
    CORREOS_CLIENT_SECRET: str = ""  # Contraseña de la app en CorreosID portal
    CORREOS_ID: str = ""  # UUID de la aplicación CorreosID (client_id real para token OAuth2)
    # API Gateway credentials (portal developers.correos.es)
    CLIENT_ID_API: str = ""
    CLIENT_SECRET_API: str = ""
    # Contract data
    CORREOS_NUM_CONTRATO: str = ""
    CORREOS_NUM_SOLICITANTE: str = ""
    CODIGO_ETIQUETADOR: str = ""
    # URLs — PRE: https://api-test.correos.es  PRO: https://api1.correos.es
    # CorreosID OAuth2 endpoint: https://apioauthcid.correos.es/Api/Authorize/Token
    CORREOS_OAUTH_URL: str = ""
    CORREOS_API_BASE: str = "https://api1.correos.es"
    # Scope for CorreosID token — leave empty if not required by your subscription
    CORREOS_SCOPE: str = ""
    # SSL verification — False for PRE/dev with self-signed certs, True for production
    CORREOS_VERIFY_SSL: bool = True
    CORREOS_SERVICE_CODE: str = "S0103"  # Paq Estándar (2–3 días hábiles)
    CORREOS_DEFAULT_WEIGHT_GRAMS: int = 500  # fallback if variant weight is missing
    # Remitente (datos de la tienda para el preregistro)
    CORREOS_SENDER_NAME: str = "CremaCuadrado"
    CORREOS_SENDER_ADDRESS: str = ""
    CORREOS_SENDER_CITY: str = ""
    CORREOS_SENDER_POSTAL_CODE: str = ""
    CORREOS_SENDER_PROVINCE: str = ""
    CORREOS_SENDER_PHONE: str = ""
    CORREOS_SENDER_EMAIL: str = "info@cremacuadrado.com"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
