"""Add user_id to batch_jobs

Revision ID: 004_add_user_id_to_batch_jobs
Revises: 003_add_job_result_pending
Create Date: 2026-07-02

Scopes batch jobs to the user who uploaded them so the list endpoint can
filter by owner.  Nullable so existing rows and future guest-adjacent cases
are handled gracefully.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "004_add_user_id_to_batch_jobs"
down_revision = "003_add_job_result_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batch_jobs",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_batch_jobs_user_id",
        "batch_jobs", "users",
        ["user_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_batch_jobs_user_id", "batch_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_jobs_user_id", table_name="batch_jobs")
    op.drop_constraint("fk_batch_jobs_user_id", "batch_jobs", type_="foreignkey")
    op.drop_column("batch_jobs", "user_id")
