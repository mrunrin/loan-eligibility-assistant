"""security_hardening: lockout counters, encrypted/pending TOTP, replay counter, status index

Revision ID: 006
Revises: 005
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.auth import decrypt_secret, encrypt_secret

    op.alter_column("users", "totp_secret", type_=sa.String(255), existing_nullable=True)
    op.add_column("users", sa.Column("totp_pending_secret", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("totp_last_counter", sa.BigInteger, nullable=True))
    for col in ("mfa_failed_attempts", "failed_login_attempts", "email_verify_attempts"):
        op.add_column("users", sa.Column(col, sa.Integer, nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    # Encrypt TOTP secrets that were stored in plain text before this revision.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL")).fetchall()
    for user_id, secret in rows:
        if decrypt_secret(secret) is None:  # not yet encrypted
            conn.execute(
                sa.text("UPDATE users SET totp_secret = :s WHERE id = :id"),
                {"s": encrypt_secret(secret), "id": user_id},
            )

    # The unique constraint on users.email already provides an index; this one was a duplicate.
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_loan_applications_status", "loan_applications", ["status"])


def downgrade() -> None:
    from app.auth import decrypt_secret

    op.drop_index("ix_loan_applications_status", table_name="loan_applications")
    op.create_index("ix_users_email", "users", ["email"])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL")).fetchall()
    for user_id, secret in rows:
        plain = decrypt_secret(secret)
        if plain is None:  # undecryptable (key changed): drop enrolment rather than leave garbage
            conn.execute(
                sa.text("UPDATE users SET totp_secret = NULL, totp_enabled = false WHERE id = :id"),
                {"id": user_id},
            )
        else:
            conn.execute(sa.text("UPDATE users SET totp_secret = :s WHERE id = :id"), {"s": plain, "id": user_id})

    op.drop_column("users", "locked_until")
    for col in ("email_verify_attempts", "failed_login_attempts", "mfa_failed_attempts"):
        op.drop_column("users", col)
    op.drop_column("users", "totp_last_counter")
    op.drop_column("users", "totp_pending_secret")
    op.alter_column("users", "totp_secret", type_=sa.String(64), existing_nullable=True)
