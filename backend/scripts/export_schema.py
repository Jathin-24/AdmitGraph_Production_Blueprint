"""Regenerate `database/schema.sql` from a live database (audit P1-6).

PLAN ground rule 6: "database/schema.sql must never diverge from Alembic
again". This script makes it a derived artefact — it is produced by running
the migrations and dumping the result, never hand-edited.

Usage (from `backend/`):

    python -m scripts.export_schema --url postgresql+asyncpg://user:pass@host:5433/dbname

    # pg_dump not on PATH (e.g. Docker Desktop on Windows) — supply the whole
    # container-local pg_dump command; connection flags are then the caller's:
    python -m scripts.export_schema --url <url> \
        --pg-dump-cmd "docker exec -e PGPASSWORD=secret postgres pg_dump -U user -h localhost -d dbname"

Steps:
1. `alembic upgrade head` against `--url` (a SCRATCH database — never prod);
2. `pg_dump --schema-only --no-owner --no-privileges` of that database;
3. write header comment (DERIVED, do not hand-edit) + dump + the
   `alembic_version` stamp row to `--output`.

The stamp row makes `psql -f database/schema.sql` on a fresh database yield
exactly the head schema: the ORM can use it immediately and a subsequent
`alembic upgrade head` is a no-op instead of a collision.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
DEFAULT_PG_DUMP = "pg_dump"
_DUMP_FLAGS = ["--schema-only", "--no-owner", "--no-privileges"]


class ExportError(RuntimeError):
    """Raised when a step (migrate/dump) fails; message includes stderr."""


def parse_database_url(url: str) -> dict[str, str]:
    """Split a SQLAlchemy PostgreSQL URL into pg_dump connection parameters."""
    if "://" not in url:
        raise ExportError(f"not a database URL: {url!r}")
    # urlparse accepts the SQLAlchemy driver suffix (postgresql+asyncpg://...).
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.path.lstrip("/"):
        raise ExportError(f"database URL must include host and database name: {url!r}")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": parsed.path.lstrip("/").split("?")[0],
    }


def build_pg_dump_command(url: str, pg_dump_cmd: str = DEFAULT_PG_DUMP) -> tuple[list[str], dict[str, str]]:
    """(argv, env) for pg_dump. `pg_dump_cmd` may be a full docker-exec line."""
    env = {**os.environ}
    if pg_dump_cmd != DEFAULT_PG_DUMP:
        # Caller-provided command runs where the server is reachable (its
        # connection flags are baked in); we only add the dump mode flags.
        return [*shlex.split(pg_dump_cmd), *_DUMP_FLAGS], env
    params = parse_database_url(url)
    env["PGPASSWORD"] = params["password"]
    return [
        pg_dump_cmd,
        *_DUMP_FLAGS,
        "-h", params["host"],
        "-p", params["port"],
        "-U", params["user"],
        "-d", params["dbname"],
    ], env


def run_migrations(url: str) -> None:
    """Apply every Alembic revision to the target database."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise ExportError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


def head_revision() -> str:
    """Current head revision id (no database needed)."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ExportError(f"alembic heads failed:\n{result.stdout}\n{result.stderr}")
    match = re.search(r"^([0-9a-f]+)\s+\(head\)", result.stdout, re.MULTILINE)
    if match is None:
        raise ExportError(f"could not parse head revision from:\n{result.stdout}")
    return match.group(1)


def dump_schema(argv: list[str], env: dict[str, str]) -> str:
    """Run pg_dump and return its stdout (the schema-only SQL)."""
    result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=300)
    if result.returncode != 0:
        raise ExportError(
            f"{' '.join(shlex.quote(a) for a in argv)} failed:\n{result.stderr}\n"
            "Is pg_dump on PATH? Otherwise pass --pg-dump-cmd (see module docstring)."
        )
    return result.stdout


def redact_command(command: str) -> str:
    """Strip secrets from the recorded regenerate hint (never commit passwords)."""
    command = re.sub(r"(PGPASSWORD=)\S+", r"\1***", command)
    # Also covers a password embedded in a URL-style connection string.
    return re.sub(r"(\w+://[^:/\s]+:)[^@\s]+@", r"\1***@", command)


def compose_schema_file(dump: str, head: str, generator: str) -> str:
    """Header + pg_dump output + alembic stamp row, ready for database/schema.sql."""
    header = (
        "-- AdmitGraph PostgreSQL schema — DERIVED ARTEFACT, DO NOT HAND-EDIT.\n"
        "-- Generated by backend/scripts/export_schema.py: alembic upgrade head\n"
        "-- followed by pg_dump --schema-only, so this file can never diverge\n"
        "-- from the Alembic history (PLAN ground rule 6).\n"
        "--\n"
        f"-- Alembic head at export time: {head}\n"
        "-- Regenerate: python -m scripts.export_schema --url <scratch-db-url>"
        f"  ({redact_command(generator)})\n"
        "--\n"
        "-- Load into a FRESH database:  psql -f database/schema.sql\n"
        "-- The stamp row below leaves alembic at head, so a later\n"
        "-- `alembic upgrade head` is a no-op instead of a collision.\n"
    )
    stamp = (
        f"\n-- Alembic version at export time ({head}).\n"
        # Fully qualified: pg_dump clears search_path above, and the ORM
        # expects `public` to be the default schema.
        f"INSERT INTO public.alembic_version (version_num) VALUES ('{head}');\n"
    )
    return header + dump + stamp


def export(url: str, output: Path, pg_dump_cmd: str = DEFAULT_PG_DUMP) -> Path:
    run_migrations(url)
    head = head_revision()
    argv, env = build_pg_dump_command(url, pg_dump_cmd)
    dump = dump_schema(argv, env)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(compose_schema_file(dump, head, pg_dump_cmd), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DATABASE_URL", ""),
        help="SQLAlchemy DATABASE_URL of the scratch database",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="where to write the schema (default: database/schema.sql)",
    )
    parser.add_argument(
        "--pg-dump-cmd",
        default=DEFAULT_PG_DUMP,
        help="pg_dump binary, or a full container command",
    )
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("--url (or DATABASE_URL) is required")
    try:
        written = export(args.url, Path(args.output), args.pg_dump_cmd)
    except ExportError as exc:
        print(f"export_schema: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
