"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-10-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ENUM types (PostgreSQL native) ---
    op.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE user_role AS ENUM ('borrower', 'officer', 'admin'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))
    op.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE application_status AS ENUM "
        "  ('draft', 'submitted', 'pre_qual_pass', 'pre_qual_fail', "
        "   'under_review', 'approved', 'rejected', 'withdrawn'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))
    op.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE prequal_decision AS ENUM ('pass', 'fail', 'manual_review'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String, nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("role", postgresql.ENUM("borrower", "officer", "admin", name="user_role", create_type=False),
                  nullable=False, server_default="borrower"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("refresh_token_hash", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # --- loan_applications ---
    op.create_table(
        "loan_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("applicant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status",
                  postgresql.ENUM("draft", "submitted", "pre_qual_pass", "pre_qual_fail",
                          "under_review", "approved", "rejected", "withdrawn",
                          name="application_status", create_type=False),
                  nullable=False, server_default="draft"),
        sa.Column("loan_amount_requested", sa.Numeric(15, 2), nullable=True),
        sa.Column("loan_purpose", sa.String(255), nullable=True),
        sa.Column("business_name", sa.String(255), nullable=True),
        sa.Column("business_type", sa.String(100), nullable=True),
        sa.Column("years_in_business", sa.Integer, nullable=True),
        sa.Column("annual_revenue", sa.Numeric(15, 2), nullable=True),
        sa.Column("monthly_emi_capacity", sa.Numeric(15, 2), nullable=True),
        sa.Column("credit_score", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_loan_applications_applicant_id", "loan_applications", ["applicant_id"])

    # --- pre_qualification_results ---
    op.create_table(
        "pre_qualification_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("loan_applications.id"), nullable=False, unique=True),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("decision",
                  postgresql.ENUM("pass", "fail", "manual_review", name="prequal_decision", create_type=False),
                  nullable=False),
        sa.Column("rule_breakdown", postgresql.JSONB, nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("loan_applications.id"), nullable=False),
        sa.Column("document_type", sa.String(100), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_documents_application_id", "documents", ["application_id"])

    # --- chat_sessions ---
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("external_session_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    # --- audit_events ---
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("chat_sessions")
    op.drop_table("documents")
    op.drop_table("pre_qualification_results")
    op.drop_table("loan_applications")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS prequal_decision")
    op.execute("DROP TYPE IF EXISTS application_status")
    op.execute("DROP TYPE IF EXISTS user_role")
