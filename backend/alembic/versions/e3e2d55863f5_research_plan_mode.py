"""research_plans.mode (live | demo)

Revision ID: e3e2d55863f5
Revises: da413f1d5652
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3e2d55863f5"
down_revision: str | None = "da413f1d5652"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_plans",
        sa.Column("mode", sa.Text(), nullable=False, server_default="live"),
    )


def downgrade() -> None:
    op.drop_column("research_plans", "mode")
