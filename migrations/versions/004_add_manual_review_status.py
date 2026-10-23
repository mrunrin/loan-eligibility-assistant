"""add_manual_review_to_application_status

Revision ID: 004
Revises: 003
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL requires IF NOT EXISTS (pg 9.3+) to make this idempotent
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'manual_review'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    # To fully roll back: DROP TYPE and recreate without 'manual_review', then
    # migrate data — not needed for dev; document the limitation.
    pass
