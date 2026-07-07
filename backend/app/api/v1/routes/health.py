from fastapi import APIRouter
from sqlalchemy import text
from app.api.deps import DbSession

router = APIRouter()


@router.get("/health", summary="Health check")
async def health_check():
    return {"status": "ok"}


@router.get("/health/db", summary="Database connectivity check")
async def db_health_check(db: DbSession):
    # 3 s local timeout: a slow/degraded DB must not make the health check hang
    await db.execute(text("SET LOCAL statement_timeout = '3000'"))
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
