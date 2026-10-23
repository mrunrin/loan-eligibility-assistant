"""add_cascade_deletes_to_fk_constraints

Revision ID: 005
Revises: 004
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # loan_applications.applicant_id → users.id  (CASCADE)
    op.drop_constraint("loan_applications_applicant_id_fkey", "loan_applications", type_="foreignkey")
    op.create_foreign_key(
        "loan_applications_applicant_id_fkey",
        "loan_applications", "users",
        ["applicant_id"], ["id"],
        ondelete="CASCADE",
    )

    # loan_applications.reviewed_by → users.id  (SET NULL)
    op.drop_constraint("loan_applications_reviewed_by_fkey", "loan_applications", type_="foreignkey")
    op.create_foreign_key(
        "loan_applications_reviewed_by_fkey",
        "loan_applications", "users",
        ["reviewed_by"], ["id"],
        ondelete="SET NULL",
    )

    # pre_qualification_results.application_id → loan_applications.id  (CASCADE)
    op.drop_constraint("pre_qualification_results_application_id_fkey", "pre_qualification_results", type_="foreignkey")
    op.create_foreign_key(
        "pre_qualification_results_application_id_fkey",
        "pre_qualification_results", "loan_applications",
        ["application_id"], ["id"],
        ondelete="CASCADE",
    )

    # documents.application_id → loan_applications.id  (CASCADE)
    op.drop_constraint("documents_application_id_fkey", "documents", type_="foreignkey")
    op.create_foreign_key(
        "documents_application_id_fkey",
        "documents", "loan_applications",
        ["application_id"], ["id"],
        ondelete="CASCADE",
    )

    # chat_sessions.user_id → users.id  (SET NULL)
    op.drop_constraint("chat_sessions_user_id_fkey", "chat_sessions", type_="foreignkey")
    op.create_foreign_key(
        "chat_sessions_user_id_fkey",
        "chat_sessions", "users",
        ["user_id"], ["id"],
        ondelete="SET NULL",
    )

    # audit_events.user_id → users.id  (SET NULL — keep audit trail, null user ref)
    op.drop_constraint("audit_events_user_id_fkey", "audit_events", type_="foreignkey")
    op.create_foreign_key(
        "audit_events_user_id_fkey",
        "audit_events", "users",
        ["user_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Restore all FKs without ondelete rules
    op.drop_constraint("loan_applications_applicant_id_fkey", "loan_applications", type_="foreignkey")
    op.create_foreign_key("loan_applications_applicant_id_fkey", "loan_applications", "users", ["applicant_id"], ["id"])

    op.drop_constraint("loan_applications_reviewed_by_fkey", "loan_applications", type_="foreignkey")
    op.create_foreign_key("loan_applications_reviewed_by_fkey", "loan_applications", "users", ["reviewed_by"], ["id"])

    op.drop_constraint("pre_qualification_results_application_id_fkey", "pre_qualification_results", type_="foreignkey")
    op.create_foreign_key("pre_qualification_results_application_id_fkey", "pre_qualification_results", "loan_applications", ["application_id"], ["id"])

    op.drop_constraint("documents_application_id_fkey", "documents", type_="foreignkey")
    op.create_foreign_key("documents_application_id_fkey", "documents", "loan_applications", ["application_id"], ["id"])

    op.drop_constraint("chat_sessions_user_id_fkey", "chat_sessions", type_="foreignkey")
    op.create_foreign_key("chat_sessions_user_id_fkey", "chat_sessions", "users", ["user_id"], ["id"])

    op.drop_constraint("audit_events_user_id_fkey", "audit_events", type_="foreignkey")
    op.create_foreign_key("audit_events_user_id_fkey", "audit_events", "users", ["user_id"], ["id"])
