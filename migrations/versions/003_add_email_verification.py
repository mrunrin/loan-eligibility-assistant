"""add_email_verification_columns

Revision ID: 003
Revises: 002
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_email_verified", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("email_verify_token_hash", sa.String(64), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verify_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verify_expires_at")
    op.drop_column("users", "email_verify_token_hash")
    op.drop_column("users", "is_email_verified")
