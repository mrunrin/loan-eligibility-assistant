"""appeals and document hashes: loan_applications.appeal_reason, documents.sha256

Revision ID: 011
Revises: 010
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("loan_applications", sa.Column("appeal_reason", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "sha256")
    op.drop_column("loan_applications", "appeal_reason")
