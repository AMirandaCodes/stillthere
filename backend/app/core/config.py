from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    APP_NAME: str = "Contact Verification Platform"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # --- Database ----------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # --- Redis / Celery ----------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # --- External APIs -----------------------------------------------------
    ANTHROPIC_API_KEY: str = ""
    SERPER_API_KEY: str = ""

    # --- CORS --------------------------------------------------------------
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Rate Limiting -----------------------------------------------------
    RATE_LIMIT_REQUESTS: int = 30
    RATE_LIMIT_PERIOD: int = 60   # seconds

    # --- Verification Pipeline Settings ------------------------------------
    MAX_SEARCH_RESULTS: int = 10
    MAX_EVIDENCE_SOURCES: int = 20
    VERIFICATION_TIMEOUT_SECONDS: int = 120

    # --- Auth / Tokens ----------------------------------------------------
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Batch processing --------------------------------------------------
    MAX_BATCH_SIZE: int = 50
    SERPER_MONTHLY_QUOTA: int = 2400   # 100-call buffer below the 2500 free-tier limit

    # --- Pagination --------------------------------------------------------
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
