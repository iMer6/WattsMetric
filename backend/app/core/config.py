from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import SecretStr, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "WattsMetric"
    
    SECRET_KEY: SecretStr = SecretStr("")
    
    ENVIRONMENT: Literal["development", "staging", "production"] = "production"
    VERSION: str = "0.0.1"
    DEBUG: bool = False
    
    # DB (Postgres + asyncpg)
    
    DB_NAME: str = "watts_metric"
    DB_USER: str = "postgres"
    DB_PASSWORD: SecretStr = SecretStr("")
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    
    # File Uploads (for Maintenance_Photos)
    
    UPLOAD_DIR: Path = BASE_DIR.parent / "media" / "photos"
    UPLOAD_URL: str = "/media/photos/"
    MAX_PHOTO_SIZE_MB: int = 10
    
    # Authorization & Cookies
    
    JWT_SECRET_KEY: SecretStr = SecretStr("")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    REFRESH_COOKIE_NAME: str = "iot_refresh_token"
    
    @property
    def COOKIE_SECURE(self) -> bool:
        """
        Automatically set Secure=True in production (when DEBUG=False)
        """
        return not self.DEBUG
    
    # noinspection SpellCheckingInspection
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    
    # Security & Rate Limiting
    
    LOGIN_MAX_ATTEMPTS: int = 10
    LOGIN_WINDOW_SECONDS: int = 300
    
    # Initial seed data
    
    SEED_DEFAULT_USERS: bool = True
    
    ADMIN_EMAIL: str = "admin@watts-metric.com"
    ADMIN_PASSWORD: SecretStr = SecretStr("")
    
    OWNER_EMAIL: str = "owner@watts-metric.com"
    OWNER_PASSWORD: SecretStr = SecretStr("")
    
    ENGINEER_EMAIL: str = "engineer@watts-metric.com"
    ENGINEER_PASSWORD: SecretStr = SecretStr("")
    
    DISPATCHER_EMAIL: str = "dispatcher@watts-metric.com"
    DISPATCHER_PASSWORD: SecretStr = SecretStr("")
    
    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def check_jwt_secret_key_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value().strip()) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long"
            )
        return v
    
    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """
        Protection against the human factor.
        Verifying critical parameters for production.
        """
        if self.DEBUG:
            return self
        
        secret_value: str = self.SECRET_KEY.get_secret_value()
        jwt_secret_value: str = self.JWT_SECRET_KEY.get_secret_value()
        
        if not secret_value:
            raise ValueError(
                "SECRET_KEY is required in production (DEBUG=False)"
            )
        
        if secret_value == jwt_secret_value:
            raise ValueError(
                "SECRET_KEY and JWT_SECRET_KEY must not be identical"
            )
        
        if not self.DB_PASSWORD.get_secret_value():
            raise ValueError(
                "DB_PASSWORD is required in production (DEBUG=False)"
            )
        
        if self.SEED_DEFAULT_USERS:
            raise ValueError(
                "SEED_DEFAULT_USERS cannot be True in production (DEBUG=False)"
            )
        
        if (
            "localhost" in self.CORS_ORIGINS
            or "127.0.0.1" in self.CORS_ORIGINS
        ):
            raise ValueError(
                "CORS_ORIGINS cannot contain localhost in production (DEBUG=False)"
            )
        
        return self
    
    @property
    def DATABASE_URL(self) -> str:
        password: str = quote_plus(self.DB_PASSWORD.get_secret_value())
        return (
            f"postgresql+asyncpg://"
            f"{self.DB_USER}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
    
    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]
    
    model_config = SettingsConfigDict(
        env_file=BASE_DIR.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings: Settings = Settings()
