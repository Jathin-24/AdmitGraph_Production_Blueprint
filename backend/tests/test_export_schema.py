"""Unit tests for scripts/export_schema.py (audit P1-6: schema is derived).

Pure functions only — no database, no pg_dump subprocess here; the full
regeneration is exercised manually/CI-side against a scratch database.
"""

from __future__ import annotations

import pytest

from scripts.export_schema import (
    ExportError,
    build_pg_dump_command,
    compose_schema_file,
    parse_database_url,
)

_URL = "postgresql+asyncpg://admitgraph:s3cr3t@localhost:5433/admitgraph"


def test_parse_database_url_extracts_pg_dump_parameters() -> None:
    params = parse_database_url(_URL)
    assert params == {
        "host": "localhost",
        "port": "5433",
        "user": "admitgraph",
        "password": "s3cr3t",
        "dbname": "admitgraph",
    }


def test_parse_database_url_rejects_non_urls() -> None:
    with pytest.raises(ExportError):
        parse_database_url("admitgraph:s3cr3t@localhost/admitgraph")


def test_pg_dump_command_carries_password_via_env_only() -> None:
    argv, env = build_pg_dump_command(_URL)
    assert argv[0] == "pg_dump"
    assert "--schema-only" in argv
    assert ["-h", "localhost"] == argv[argv.index("-h") : argv.index("-h") + 2]
    assert env["PGPASSWORD"] == "s3cr3t"
    # The password never appears on the command line (visible in `ps`).
    assert "s3cr3t" not in " ".join(argv)


def test_custom_pg_dump_command_keeps_caller_connection_flags() -> None:
    argv, _env = build_pg_dump_command(
        _URL, pg_dump_cmd="docker exec -e PGPASSWORD=x pg pg_dump -U u -h localhost -d db"
    )
    assert argv[:3] == ["docker", "exec", "-e"]
    assert argv[-3:] == ["--schema-only", "--no-owner", "--no-privileges"]
    # Nothing is appended from the URL: connection flags are the caller's.
    assert argv.count("-U") == 1
    assert argv.count("-d") == 1


def test_composed_file_has_derived_header_stamp_and_dump() -> None:
    text = compose_schema_file("CREATE TABLE t (id int);\n", "abc123def456", "pg_dump")
    assert "DERIVED ARTEFACT, DO NOT HAND-EDIT" in text
    assert "Alembic head at export time: abc123def456" in text
    assert "CREATE TABLE t (id int);" in text
    assert "INSERT INTO public.alembic_version (version_num) VALUES ('abc123def456');" in text
    # Header must come first so `psql -f` readers see the provenance up top.
    assert text.index("DERIVED") < text.index("CREATE TABLE")
    assert text.index("CREATE TABLE") < text.index("INSERT INTO public.alembic_version")


def test_composer_redacts_passwords_from_recorded_command() -> None:
    # The regenerate hint names the pg_dump command actually used — it must
    # never echo a password into a committed file.
    text = compose_schema_file(
        "CREATE TABLE t (id int);\n",
        "abc123def456",
        "docker exec -e PGPASSWORD=hunter2 pg pg_dump -U u -h localhost -d db",
    )
    assert "hunter2" not in text
    assert "PGPASSWORD=***" in text
    url_text = compose_schema_file(
        "CREATE TABLE t (id int);\n",
        "abc123def456",
        "pg_dump --url postgresql://user:hunter2@host/db",
    )
    assert "hunter2" not in url_text
