from fastapi import APIRouter
from sqlalchemy import text
from app.api.deps import DbSession

router = APIRouter()


@router.get("/health", summary="Health check")
async def health_check(db: DbSession):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
