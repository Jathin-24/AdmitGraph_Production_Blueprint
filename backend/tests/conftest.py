"""Shared fixtures.

`live_db` provisions a scratch PostgreSQL database (admitgraph_test), applies
all Alembic migrations to it, and points the app's settings/engine at it for
the duration of the test session. If PostgreSQL is unreachable the fixture
skips the requesting tests, so unit/contract tests still run on CI without a
database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

DEFAULT_TEST_URL = "postgresql+asyncpg://admitgraph:admitgraph_dev_password@localhost:5433/admitgraph_test"
TEST_URL = os.environ.get("AGRAPH_TEST_DATABASE_URL", DEFAULT_TEST_URL)
REAL_URL = os.environ.get("DATABASE_URL", "")


def _sync_admin_url(test_url: str) -> tuple[str, str, str, str]:
    """Return (admin url to postgres maintenance db, user, password, dbname)."""
    bare = test_url.split("://", 1)[1]
    cred, hostpart = bare.rsplit("@", 1)
    user, _, password = cred.partition(":")
    host, _, rest = hostpart.partition("/")
    dbname = rest.split("?")[0] or "postgres"
    return (f"postgresql+psycopg://{cred}@{host}/postgres", user, password, dbname)


def _ensure_database(test_url: str) -> None:
    admin_url, _user, _password, dbname = _sync_admin_url(test_url)
    from sqlalchemy import create_engine, text

    engine = create_engine(admin_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


def _reset_schema_and_migrate(test_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": test_url, "PYTHONPATH": str(BACKEND_DIR)}
    # Drop everything in the *test* database so each session starts from a
    # known, migrated state (never touch the maintenance database).
    from sqlalchemy import create_engine, text

    admin_url, _user, _password, dbname = _sync_admin_url(test_url)
    test_admin_url = admin_url[: -len("postgres")] + dbname
    engine = create_engine(test_admin_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.commit()
    finally:
        engine.dispose()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def live_db() -> Iterator[str]:
    """Provision + migrate the scratch DB, repoint settings at it, then restore."""
    try:
        _ensure_database(TEST_URL)
        _reset_schema_and_migrate(TEST_URL)
    except Exception as exc:  # noqa: BLE001 - no server? skip, don't fail the suite
        pytest.skip(f"PostgreSQL not available for integration tests: {exc}")

    os.environ["DATABASE_URL"] = TEST_URL
    # Disable real provider calls from integration tests: honest "no claims" path.
    os.environ["LLM_PROVIDERS"] = ""
    os.environ["LLM_API_KEYS"] = ""
    os.environ["LLM_MODELS"] = ""
    os.environ["SERPAPI_API_KEY"] = ""

    from app.core.config import get_settings
    from app.db.session import dispose_engine

    get_settings.cache_clear()
    dispose_engine()

    yield TEST_URL

    dispose_engine()
    get_settings.cache_clear()
    if REAL_URL:
        os.environ["DATABASE_URL"] = REAL_URL
    else:
        os.environ.pop("DATABASE_URL", None)
    for var in ("LLM_PROVIDERS", "LLM_API_KEYS", "LLM_MODELS", "SERPAPI_API_KEY"):
        os.environ.pop(var, None)


@pytest.fixture
async def db_session(live_db: str) -> AsyncIterator:
    """A fresh session against the scratch database for one test.

    The engine pool is disposed (within the test's still-open event loop)
    on teardown: asyncpg connections are loop-bound and pytest-asyncio creates
    a new loop per test, so pooled connections must not outlive their test.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import get_engine

    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with maker() as session:
        yield session
    await get_engine().dispose()
