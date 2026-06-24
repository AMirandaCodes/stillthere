"""Initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-06-23

Tables created (in FK-dependency order):
  1. contacts
  2. companies
  3. batch_jobs
  4. searches          (FK: contacts, companies, batch_jobs)
  5. verification_results (FK: searches)
  6. evidence_sources  (FK: verification_results)
  7. job_results       (FK: batch_jobs, searches, verification_results)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── contacts ──────────────────────────────────────────────────────────────
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("email", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_contacts_normalized_name", "contacts", ["normalized_name"])
    op.create_index("ix_contacts_email", "contacts", ["email"], unique=True)

    # ── companies ─────────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("website", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_companies_normalized_name", "companies", ["normalized_name"])
    op.create_index("ix_companies_domain", "companies", ["domain"])

    # ── batch_jobs ────────────────────────────────────────────────────────────
    op.create_table(
        "batch_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint("status IN ('queued','running','complete','failed')"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("total_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("successful_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unclear_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_batch_jobs_status", "batch_jobs", ["status"])
    op.create_index("ix_batch_jobs_created_at", "batch_jobs", ["created_at"])

    # ── searches ──────────────────────────────────────────────────────────────
    op.create_table(
        "searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("submitted_email", sa.String(500), nullable=True),
        sa.Column(
            "source",
            sa.String(20),
            sa.CheckConstraint("source IN ('single','batch')"),
            nullable=False,
            server_default="single",
        ),
        sa.Column(
            "batch_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batch_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_searches_contact_id", "searches", ["contact_id"])
    op.create_index("ix_searches_company_id", "searches", ["company_id"])
    op.create_index("ix_searches_batch_job_id", "searches", ["batch_job_id"])
    op.create_index("ix_searches_created_at", "searches", ["created_at"])

    # ── verification_results ──────────────────────────────────────────────────
    op.create_table(
        "verification_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("searches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint("status IN ('pending','running','complete','failed')"),
            nullable=False,
            server_default="pending",
        ),
        # Tri-state report fields — all default to 'unclear'
        sa.Column(
            "person_found",
            sa.String(10),
            sa.CheckConstraint("person_found IN ('yes','no','unclear')"),
            nullable=False,
            server_default="unclear",
        ),
        sa.Column(
            "appears_associated",
            sa.String(10),
            sa.CheckConstraint("appears_associated IN ('yes','no','unclear')"),
            nullable=False,
            server_default="unclear",
        ),
        sa.Column(
            "found_on_website",
            sa.String(10),
            sa.CheckConstraint("found_on_website IN ('yes','no','unclear')"),
            nullable=False,
            server_default="unclear",
        ),
        sa.Column(
            "company_active",
            sa.String(10),
            sa.CheckConstraint("company_active IN ('yes','no','unclear')"),
            nullable=False,
            server_default="unclear",
        ),
        sa.Column(
            "email_match",
            sa.String(10),
            sa.CheckConstraint("email_match IN ('yes','no','unclear')"),
            nullable=False,
            server_default="unclear",
        ),
        sa.Column("confidence_score", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column(
            "confidence_level",
            sa.String(10),
            sa.CheckConstraint("confidence_level IN ('high','medium','low')"),
            nullable=False,
            server_default="low",
        ),
        sa.Column(
            "useful_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "raw_search_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_verification_results_search_id", "verification_results", ["search_id"])
    op.create_index("ix_verification_results_status", "verification_results", ["status"])
    op.create_index("ix_verification_results_confidence_score", "verification_results", ["confidence_score"])
    op.create_index("ix_verification_results_created_at", "verification_results", ["created_at"])

    # ── evidence_sources ──────────────────────────────────────────────────────
    op.create_table(
        "evidence_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "verification_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("verification_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("explanation", sa.Text, nullable=True),
        sa.Column(
            "source_type",
            sa.String(50),
            sa.CheckConstraint(
                "source_type IN ('search_result','company_website','professional_profile',"
                "'business_directory','other')"
            ),
            nullable=False,
            server_default="search_result",
        ),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evidence_sources_verification_result_id",
        "evidence_sources",
        ["verification_result_id"],
    )

    # ── job_results ───────────────────────────────────────────────────────────
    op.create_table(
        "job_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batch_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("searches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "verification_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("verification_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint("status IN ('success','failed','skipped')"),
            nullable=False,
            server_default="success",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "raw_csv_row",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_job_results_batch_job_id", "job_results", ["batch_job_id"])
    op.create_index("ix_job_results_search_id", "job_results", ["search_id"])
    op.create_index("ix_job_results_status", "job_results", ["status"])


def downgrade() -> None:
    # Drop in reverse FK-dependency order
    op.drop_table("job_results")
    op.drop_table("evidence_sources")
    op.drop_table("verification_results")
    op.drop_table("searches")
    op.drop_table("batch_jobs")
    op.drop_table("companies")
    op.drop_table("contacts")
