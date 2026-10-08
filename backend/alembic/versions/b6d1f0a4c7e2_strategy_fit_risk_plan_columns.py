"""strategy scoring/provenance columns

- fit_assessments.profile_snapshot: the requirement values/statuses, weights
  and scoring_version used for the score, so it can be recalculated.
- risks.requirement_id: the eligibility requirement whose evaluation raised
  the risk (SET NULL: requirements may be re-synced from newer evidence).
- application_plans.reasons: the top dimension reasons shown on the card.

Revision ID: b6d1f0a4c7e2
Revises: 743d20854d53
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6d1f0a4c7e2"
down_revision: str | None = "743d20854d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fit_assessments",
        sa.Column(
            "profile_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "risks",
        sa.Column("requirement_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "risks_requirement_id_fkey",
        "risks",
        "requirements",
        ["requirement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "application_plans",
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("application_plans", "reasons")
    op.drop_constraint("risks_requirement_id_fkey", "risks", type_="foreignkey")
    op.drop_column("risks", "requirement_id")
    op.drop_column("fit_assessments", "profile_snapshot")
