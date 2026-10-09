"""P1-8 daily search budget — pure helpers and settings (no database).

The endpoint behaviour (429 BUDGET_EXCEEDED at the cap, admission below it)
is covered by integration/test_research_budget_db.py against real PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.budget import utc_day_start
from app.core.config import Settings, get_settings


def test_utc_day_start_is_midnight_utc() -> None:
    moment = datetime(2026, 10, 9, 13, 45, 12, tzinfo=UTC)
    assert utc_day_start(moment) == datetime(2026, 10, 9, 0, 0, 0, tzinfo=UTC)


def test_daily_budget_settings_defaults() -> None:
    # Constructed without the .env file so a developer's local overrides
    # cannot change what "default" means for the contract.
    settings = Settings(_env_file=None)
    assert settings.daily_search_budget == 500
    assert settings.daily_search_budget_per_user == 0  # 0 = unlimited


def test_daily_budget_settings_read_the_documented_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGRAPH_DAILY_SEARCH_BUDGET", "42")
    monkeypatch.setenv("AGRAPH_DAILY_SEARCH_BUDGET_PER_USER", "7")
    assert Settings(_env_file=None).daily_search_budget == 42
    assert Settings(_env_file=None).daily_search_budget_per_user == 7


async def test_unlimited_budget_never_runs_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 = unlimited: the guard must not even touch the database."""
    from app.core.budget import reject_if_budget_spent

    class _BoomSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("an unlimited budget must not run a COUNT query")

    settings = get_settings()
    monkeypatch.setattr(settings, "daily_search_budget", 0)
    monkeypatch.setattr(settings, "daily_search_budget_per_user", 0)
    await reject_if_budget_spent(_BoomSession(), uuid.uuid4())  # type: ignore[arg-type]
