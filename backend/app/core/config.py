from pathlib import Path
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "WattsMetric"
    
    SECRET_KEY: SecretStr = SecretStr("")
    
    ENVIRONMENT: str = "production"
    VERSION: str = "0.0.1"
    DEBUG: bool = False
    ALGORITHM: str = "HS256" # for generating and verifying JWT tokens
    
    # DB (Postgres + asyncpg)
    DB_NAME: str = "watts_metric"
    DB_USER: str = "postgres"
    DB_PASSWORD: SecretStr = SecretStr("")
    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"
    
    # Authorization & Cookies
    
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
    COOKIE_SAMESITE: str = "lax"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    
    # Security & Rate Limiting
    
    LOGIN_MAX_ATTEMPTS: int = 10
    LOGIN_WINDOW_SECONDS: int = 300
    
    # File Uploads (for Maintenance_Photos)
    
    UPLOAD_DIR: Path = BASE_DIR / "media" / "photos"
    MAX_PHOTO_SIZE_MB: int = 10
    
    # Initial seed data
    
    SEED_DEFAULT_USERS: bool = True
    
    ADMIN_EMAIL: str = "admin@watts-metric.com"
    ADMIN_PASSWORD: SecretStr = SecretStr("")
    
    OWNER_EMAIL: str = "owner@watts-metric.com"
    OWNER_PASSWORD: SecretStr = SecretStr("")
    
    ENGINEER_EMAIL: str = "engineer@watts-metric.com"
    ENGINEER_PASSWORD: SecretStr = SecretStr("")
    
    DISPATCHER_EMAIl: str = "dispatcher@watts-metric.com"
    DISPATCHER_PASSWORD: SecretStr = SecretStr("")
    
    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """
        Protection against the human factor.
        Verifying critical parameters for production.
        """
        if self.DEBUG:
            return self
        
        if not self.SECRET_KEY.get_secret_value():
            raise ValueError("SECRET_KEY is required in production (DEBUG=False)")
        if not self.DB_PASSWORD.get_secret_value():
            raise ValueError("DB_PASSWORD is required in production (DEBUG=False)")
        
        if self.SEED_DEFAULT_USERS:
            raise ValueError(
                "SEED_DEFAULT_USERS cannot be True in production (DEBUG=False)"
            )
        
        if ("localhost" in self.CORS_ORIGINS
            or "127.0.0.1" in self.CORS_ORIGINS
        ):
            raise ValueError(
                "CORS_ORIGINS cannot contain localhost in production (DEBUG=False)"
            )
        
        return self
    
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://"
            f"{self.DB_USER}:{self.DB_PASSWORD.get_secret_value()}"
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