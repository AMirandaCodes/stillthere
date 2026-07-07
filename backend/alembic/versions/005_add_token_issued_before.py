"""Add token_issued_before to users

Revision ID: 005_add_token_issued_before
Revises: 004_add_user_id_to_batch_jobs
Create Date: 2026-07-07

Adds a nullable timestamp to the users table that, when set, causes
get_current_user to reject any access token issued before that moment.
Used as a prerequisite for a future password-change endpoint — setting
token_issued_before=now() on credential change invalidates all live
access tokens for that user without requiring a JWT blocklist (AUTH-06).
"""
import sqlalchemy as sa
from alembic import op

revision = "005_add_token_issued_before"
down_revision = "004_add_user_id_to_batch_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_issued_before",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_issued_before")
