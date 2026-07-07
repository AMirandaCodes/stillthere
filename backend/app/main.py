from contextlib import asynccontextmanager
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.routes import admin, auth, batch, companies, contacts, health, verifications
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.rate_limiting import limiter
from app.db.session import engine

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Starting StillThere", version=settings.APP_VERSION)

    # Initialise Redis connection pool (used by CacheService and Celery)
    try:
        app.state.redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=2.0,          # abort a hung command after 2 s
            socket_connect_timeout=2.0,  # fail fast if Redis is unreachable
        )
        await app.state.redis.ping()
        logger.info("Redis connected", url=settings.REDIS_URL)
    except Exception as exc:
        logger.warning("Redis unavailable — cache disabled", error=str(exc))
        app.state.redis = None

    yield

    if app.state.redis:
        await app.state.redis.aclose()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Verify whether a business contact is likely still employed at a company "
        "using publicly available information sources."
    ),
    # Disable interactive docs in production to avoid exposing the full API schema
    # to unauthenticated visitors (SEC-06). Set APP_ENV=production on Render.
    docs_url="/api/docs"        if not settings.is_production else None,
    redoc_url="/api/redoc"      if not settings.is_production else None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = uuid.uuid4().hex[:8]
        structlog.contextvars.bind_contextvars(request_id=req_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = req_id
        return response


app.add_middleware(RequestIDMiddleware)


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject JSON requests whose Content-Length exceeds 1 MB (SEC-04).

    Multipart requests (CSV upload) are excluded here — BatchService enforces
    a 5 MB streaming limit on those via its own chunked-read logic.
    """
    _MAX_JSON_BYTES = 1 * 1024 * 1024

    async def dispatch(self, request: Request, call_next):
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self._MAX_JSON_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add browser security headers to every response (SEC-01).

    Placed as the outermost middleware so headers are injected on all
    responses including 413/429/500 from inner middleware and handlers.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


app.add_middleware(ContentSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# ── Global exception handlers ──────────────────────────────────────────────────

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    req_id = structlog.contextvars.get_contextvars().get("request_id", "unknown")
    logger.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "request_id": req_id,
        },
    )


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(health.router,         prefix="/api/v1",                tags=["health"])
app.include_router(auth.router,           prefix="/api/v1/auth",           tags=["auth"])
app.include_router(verifications.router,  prefix="/api/v1/verifications",  tags=["verifications"])
app.include_router(contacts.router,       prefix="/api/v1/contacts",       tags=["contacts"])
app.include_router(companies.router,      prefix="/api/v1/companies",      tags=["companies"])
app.include_router(batch.router,          prefix="/api/v1/batch",          tags=["batch"])
app.include_router(admin.router,          prefix="/api/v1/admin",          tags=["admin"])
