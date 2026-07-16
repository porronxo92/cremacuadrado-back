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
    
    # Tax
    TAX_RATE: float = 0.21  # 21% IVA
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 12
    MAX_PAGE_SIZE: int = 100
    
    # Email — set EMAIL_ENABLED=True to send real emails.
    # Two mailboxes route outgoing mail by category (see app/services/email.py):
    #   Pedidos@cremacuadrado.com — confirmación de pedido, fallo de pago, envío, factura
    #   Info@cremacuadrado.com    — bienvenida, verificación, newsletter, contacto, B2B, y todo lo demás
    EMAIL_ENABLED: bool = False
    SITE_URL: str = "http://localhost:4200"

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

    # Security headers
    SECURE_HEADERS: bool = True  # Set False only for local dev if needed

    # Correos España — set CORREOS_ENABLED=True with a signed contract to call the real API.
    # While False, shipment creation runs in mock mode (returns a fake localizador).
    CORREOS_ENABLED: bool = False
    CORREOS_CLIENT_ID: str = ""
    CORREOS_CLIENT_SECRET: str = ""
    CORREOS_NUM_CONTRATO: str = ""
    CORREOS_NUM_SOLICITANTE: str = ""
    CORREOS_OAUTH_URL: str = "https://apioauthcid.correos.es/cid/oauth2/v1/token"
    CORREOS_API_BASE: str = "https://apicorp.correos.es"
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
