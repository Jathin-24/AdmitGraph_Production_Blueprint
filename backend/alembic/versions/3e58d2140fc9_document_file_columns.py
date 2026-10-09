"""document file upload columns (P2-15)

All three are NULL until a file exists:
- documents.file_path: stored path relative to the upload root (uuid name).
- documents.file_name: the ORIGINAL filename kept for display/download.
- documents.file_size: bytes as stored.

Revision ID: 3e58d2140fc9
Revises: 9c31f5ab77d4
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e58d2140fc9"
down_revision: str | None = "9c31f5ab77d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("file_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("file_name", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("file_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "file_size")
    op.drop_column("documents", "file_name")
    op.drop_column("documents", "file_path")
