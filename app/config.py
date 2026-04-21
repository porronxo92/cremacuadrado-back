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
    DEBUG: bool = True
    
    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cremacuadrado"
    
    # JWT Authentication
    SECRET_KEY: str  # Required — set via .env, never hardcode
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:4200", "http://127.0.0.1:4200"]
    
    # Shipping (MVP: fixed price)
    SHIPPING_COST: float = 4.95
    FREE_SHIPPING_THRESHOLD: float = 50.0
    
    # Tax
    TAX_RATE: float = 0.21  # 21% IVA
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 12
    MAX_PAGE_SIZE: int = 100
    
    # Email (MVP: logs only)
    EMAIL_ENABLED: bool = False
    EMAIL_FROM: str = "info@cremacuadrado.com"
    
    # Admin
    ADMIN_EMAIL: str = "admin@cremacuadrado.com"
    ADMIN_PASSWORD: str  # Required — set via .env, never hardcode


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
