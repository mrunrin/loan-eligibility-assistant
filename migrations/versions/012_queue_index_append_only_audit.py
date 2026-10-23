"""officer-queue index and append-only audit_events

Revision ID: 012
Revises: 011
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The officer queue filters by status and sorts by created_at.
    op.create_index("ix_loan_applications_status_created", "loan_applications", ["status", "created_at"])

    # audit_events is a write-once log: rows can be added, never changed or removed.
    # The one allowed UPDATE is the users FK's ON DELETE SET NULL, which detaches a deleted
    # account from its history (personal-data erasure) while keeping every event.
    op.execute("""
        CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            -- Checked on its own first: NEW is unset for DELETE, and OLD/NEW both for TRUNCATE.
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP;
            END IF;
            IF OLD.user_id IS NOT NULL AND NEW.user_id IS NULL
               AND NEW.id = OLD.id
               AND NEW.event_type IS NOT DISTINCT FROM OLD.event_type
               AND NEW.payload IS NOT DISTINCT FROM OLD.payload
               AND NEW.request_id IS NOT DISTINCT FROM OLD.request_id
               AND NEW.created_at = OLD.created_at THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.drop_index("ix_loan_applications_status_created", table_name="loan_applications")
