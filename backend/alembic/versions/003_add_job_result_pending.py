"""Add PENDING status to job_results

Revision ID: 003_add_job_result_pending
Revises: 002_add_users_and_auth
Create Date: 2026-06-24

Adds 'pending' as a valid value for job_results.status so that BatchService
can pre-create job_result records before the Celery task runs.  The original
CHECK CONSTRAINT only allowed ('success','failed','skipped').

The constraint was created without an explicit name in migration 001, so
PostgreSQL assigned an auto-generated name.  This migration finds and drops
whichever constraint covers the status column and replaces it with the new one.
"""
from alembic import op

revision = "003_add_job_result_pending"
down_revision = "002_add_users_and_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the auto-named check constraint on job_results.status (name unknown;
    # find it by matching any check constraint on this table that references 'status').
    op.execute("""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN (
                SELECT conname
                FROM   pg_constraint
                WHERE  conrelid = 'job_results'::regclass
                  AND  contype  = 'c'
                  AND  pg_get_constraintdef(oid) LIKE '%status%'
            ) LOOP
                EXECUTE 'ALTER TABLE job_results DROP CONSTRAINT ' || quote_ident(r.conname);
            END LOOP;
        END
        $$;
    """)
    op.execute("""
        ALTER TABLE job_results
            ADD CONSTRAINT job_results_status_check
            CHECK (status IN ('pending', 'success', 'failed', 'skipped'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE job_results DROP CONSTRAINT IF EXISTS job_results_status_check")
    op.execute("""
        ALTER TABLE job_results
            ADD CONSTRAINT job_results_status_check
            CHECK (status IN ('success', 'failed', 'skipped'))
    """)
    # Rows with status='pending' at downgrade time would violate the restored
    # constraint; callers must purge or update those rows before downgrading.
