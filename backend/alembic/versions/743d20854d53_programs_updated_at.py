"""programs.updated_at — required by the programs_updated_at trigger

The extension_and_updated_at_triggers migration (5d8e565cd96c) installs a
BEFORE UPDATE trigger on programs that sets NEW.updated_at, but the table
never had the column, so every UPDATE on programs failed. Program enrichment
(field/tuition updates during research runs) is the first code to update the
table, which surfaced the latent bug.

Revision ID: 743d20854d53
Revises: e3e2d55863f5
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "743d20854d53"
down_revision: str | None = "e3e2d55863f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "programs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("programs", "updated_at")
