from celery import Celery
from app.core.config import get_settings
import app.db.registry  # noqa: F401 — registers all SQLAlchemy models before any task runs

settings = get_settings()

celery_app = Celery(
    "cvp",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.verification_tasks", "app.tasks.batch_tasks"],
)

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
)
