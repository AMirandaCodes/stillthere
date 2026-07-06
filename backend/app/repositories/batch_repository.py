from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch_job import BatchJob
from app.models.enums import BatchJobStatus
from app.repositories.base import BaseRepository


class BatchRepository(BaseRepository[BatchJob]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BatchJob, session)

    async def get_job(self, job_id: UUID) -> BatchJob | None:
        return await self.session.get(BatchJob, job_id)

    async def set_failed(self, job_id: UUID) -> None:
        await self.session.execute(
            update(BatchJob)
            .where(BatchJob.id == job_id)
            .values(status=BatchJobStatus.FAILED)
        )
