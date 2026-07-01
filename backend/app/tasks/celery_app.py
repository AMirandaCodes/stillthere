import ssl

from celery import Celery
from app.core.config import get_settings
import app.db.registry  # noqa: F401 — registers all SQLAlchemy models before any task runs

settings = get_settings()

celery_app = Celery(
    "stillthere",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.verification_tasks", "app.tasks.batch_tasks"],
)

_ssl_opts = {"ssl_cert_reqs": ssl.CERT_NONE}
_use_ssl = settings.CELERY_BROKER_URL.startswith("rediss://")

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # fair dispatch for long-running tasks
    task_soft_time_limit=settings.VERIFICATION_TIMEOUT_SECONDS,
    task_time_limit=settings.VERIFICATION_TIMEOUT_SECONDS + 30,
    **({"broker_use_ssl": _ssl_opts, "redis_backend_use_ssl": _ssl_opts} if _use_ssl else {}),
)
