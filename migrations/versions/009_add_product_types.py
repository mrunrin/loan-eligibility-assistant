"""add product_type and personal-loan / credit-card applicant fields

Revision ID: 009
Revises: 008
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

product_type = postgresql.ENUM("business_loan", "personal_loan", "credit_card", name="product_type")

NEW_COLUMNS = [
    sa.Column("employment_type", sa.String(20), nullable=True),
    sa.Column("employer_name", sa.String(255), nullable=True),
    sa.Column("date_of_birth", sa.Date(), nullable=True),
    sa.Column("work_experience_years", sa.Integer(), nullable=True),
    sa.Column("current_job_years", sa.Integer(), nullable=True),
    sa.Column("monthly_income", sa.Numeric(15, 2), nullable=True),
    sa.Column("existing_emis", sa.Numeric(15, 2), nullable=True),
]


def upgrade() -> None:
    product_type.create(op.get_bind(), checkfirst=True)
    # server_default backfills every existing row: all applications so far are business loans.
    op.add_column(
        "loan_applications",
        sa.Column("product_type", product_type, nullable=False, server_default="business_loan"),
    )
    op.create_index("ix_loan_applications_product_type", "loan_applications", ["product_type"])
    for column in NEW_COLUMNS:
        op.add_column("loan_applications", column)
    op.create_check_constraint(
        "ck_loan_applications_employment_type",
        "loan_applications",
        "employment_type IS NULL OR employment_type IN ('salaried', 'self_employed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_loan_applications_employment_type", "loan_applications", type_="check")
    for column in reversed(NEW_COLUMNS):
        op.drop_column("loan_applications", column.name)
    op.drop_index("ix_loan_applications_product_type", table_name="loan_applications")
    op.drop_column("loan_applications", "product_type")
    product_type.drop(op.get_bind(), checkfirst=True)
