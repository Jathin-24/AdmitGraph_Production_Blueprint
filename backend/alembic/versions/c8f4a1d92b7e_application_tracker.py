"""application tracker rows (W12)

Student-owned applications: university/program label, status, portal URL,
free-form notes, submitted/decision timestamps.

- application_status is a native Postgres enum with the contract's
  lower-case values (draft|submitted|interview|offer|rejected|waitlist|
  withdrawn) — the API answers 422 for anything else before a row exists.
- updated_at is maintained by SQL, not by the ORM: the shared
  set_updated_at() trigger function from 5d8e565cd96c is attached to this
  table too, like every other table carrying the column.

Revision ID: c8f4a1d92b7e
Revises: 3e58d2140fc9
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8f4a1d92b7e"
down_revision: str | None = "3e58d2140fc9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_STATUSES = (
    "draft",
    "submitted",
    "interview",
    "offer",
    "rejected",
    "waitlist",
    "withdrawn",
)


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("university", sa.Text(), nullable=False),
        sa.Column("program_name", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*APPLICATION_STATUSES, name="application_status"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # The tracker groups and filters by owner+status on every list request.
    op.create_index(
        "idx_applications_user_status",
        "applications",
        ["user_id", "status"],
    )
    op.execute(
        "CREATE TRIGGER applications_updated_at BEFORE UPDATE ON applications "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS applications_updated_at ON applications")
    op.drop_index("idx_applications_user_status", table_name="applications")
    op.drop_table("applications")
    # Postgres enum types outlive their table; leaving application_status
    # behind would make a later re-run of this upgrade fail on CREATE TYPE.
    sa.Enum(name="application_status").drop(op.get_bind(), checkfirst=True)
