"""extension and updated_at triggers

Revision ID: 5d8e565cd96c
Revises: 13a74ba738c5
"""
from collections.abc import Sequence

from alembic import op

revision: str = "5d8e565cd96c"
down_revision: str | None = "13a74ba738c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERED_TABLES = [
    ("users", "users_updated_at"),
    ("student_profiles", "profiles_updated_at"),
    ("profile_preferences", "preferences_updated_at"),
    ("institutions", "institutions_updated_at"),
    ("programs", "programs_updated_at"),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        """
    )
    for table, trigger in TRIGGERED_TABLES:
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table, trigger in TRIGGERED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
