"""one-time password reset / email verification tokens (P2-14)

- users.password_changed_at: stamped by POST /auth/reset (NULL = never).
- users.email_verified_at: stamped by POST /auth/verify (NULL = unverified).
- auth_tokens: single-use, hashed, expiring token rows (PASSWORD_RESET /
  EMAIL_VERIFY). Only the SHA-256 hash of the raw token is stored.

Revision ID: 9c31f5ab77d4
Revises: b6d1f0a4c7e2
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c31f5ab77d4"
down_revision: str | None = "b6d1f0a4c7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "auth_tokens",
        # server default mirrors models._uuid_pk(): the ORM relies on the
        # database to mint the primary key, so without it every INSERT 500s.
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="auth_tokens_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="auth_tokens_token_hash_key"),
    )
    op.create_index(
        "ix_auth_tokens_user_purpose", "auth_tokens", ["user_id", "purpose"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_user_purpose", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "password_changed_at")
