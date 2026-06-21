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
    FREE_SHIPPING_THRESHOLD: float = 50.0
    
    # Tax
    TAX_RATE: float = 0.21  # 21% IVA
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 12
    MAX_PAGE_SIZE: int = 100
    
    # Email — set EMAIL_ENABLED=True and configure SMTP to send real emails
    # Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=tu@gmail.com
    #        SMTP_PASSWORD=app-password-16-chars (Google Account › Security › App passwords)
    EMAIL_ENABLED: bool = False
    EMAIL_FROM: str = "info@cremacuadrado.com"
    EMAIL_FROM_NAME: str = "CremaCuadrado"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SITE_URL: str = "http://localhost:4200"
    
    # Admin
    ADMIN_EMAIL: str = "admin@cremacuadrado.com"
    ADMIN_PASSWORD: str  # Required — set via .env, never hardcode

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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
