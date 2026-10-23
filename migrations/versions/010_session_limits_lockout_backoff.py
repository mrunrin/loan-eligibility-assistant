"""session limits and lockout backoff: users.lockout_count, session_started_at, last_seen_at

Revision ID: 010
Revises: 009
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("lockout_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_seen_at")
    op.drop_column("users", "session_started_at")
    op.drop_column("users", "lockout_count")
