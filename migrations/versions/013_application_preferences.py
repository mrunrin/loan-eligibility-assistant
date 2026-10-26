"""optional product preferences on loan applications

Revision ID: 013
Revises: 012
Create Date: 2026-10-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: existing applications simply have no preferences.
    op.add_column("loan_applications", sa.Column("preferences", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("loan_applications", "preferences")
