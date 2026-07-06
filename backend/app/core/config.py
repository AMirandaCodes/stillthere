import json
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_KEYS = {"change-me-in-production", "change-me-in-production-use-a-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    APP_NAME: str = "StillThere"
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

    # --- Per-user daily limits -------------------------------------------
    DAILY_VERIFICATIONS_USER: int = 5
    DAILY_VERIFICATIONS_GUEST: int = 1
    DAILY_BATCH_UPLOADS_USER: int = 2

    # --- Pagination --------------------------------------------------------
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

        if not isinstance(v, str):
            return v

        # Normalize scheme for asyncpg
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Strip libpq-only query params that asyncpg rejects.
        # Neon connection strings include sslmode, channel_binding, options, etc.
        _LIBPQ_ONLY = {"sslmode", "channel_binding", "options"}
        _SSL_REQUIRING = {"require", "verify-ca", "verify-full", "prefer"}

        parsed = urlparse(v)
        params = parse_qs(parsed.query, keep_blank_values=True)

        ssl_mode = (params.pop("sslmode", [None])[0] or "")
        for key in _LIBPQ_ONLY - {"sslmode"}:
            params.pop(key, None)

        if ssl_mode in _SSL_REQUIRING and "ssl" not in params:
            params["ssl"] = ["require"]

        new_query = urlencode({k: vals[0] for k, vals in params.items()})
        return urlunparse(parsed._replace(query=new_query))


    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.APP_ENV != "production":
            return self
        if self.SECRET_KEY in _PLACEHOLDER_KEYS or len(self.SECRET_KEY) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters and cannot be a placeholder value. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY must be set in production (APP_ENV=production)")
        if not self.SERPER_API_KEY:
            raise ValueError("SERPER_API_KEY must be set in production (APP_ENV=production)")
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
