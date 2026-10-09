"""The image must never bake secrets or local state (audit P0-4 / PLAN P0-4).

backend/Dockerfile used to `COPY . .`, which put backend/.env (live
SerpApi/OpenRouter/Groq keys + JWT secret), .venv/, var/outbox (100+ files),
uvicorn logs, caches and egg-info into the image. These tests pin both the
COPY allowlist and the .dockerignore coverage so it cannot regress.
"""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Everything that must stay out of the image (regression list from the audit).
_FORBIDDEN_IN_IMAGE = (
    ".env",
    ".venv",
    "var/",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".egg-info",
    "uvicorn",
    ".log",
    "answers.json",
    "profile.json",
)


def test_dockerignore_exists_and_covers_every_secret_and_state_path() -> None:
    dockerignore = BACKEND_DIR / ".dockerignore"
    assert dockerignore.exists(), "backend/.dockerignore is required (audit P0-4)"
    content = dockerignore.read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_IN_IMAGE:
        assert pattern in content, f".dockerignore does not exclude {pattern!r}"


def test_dockerfile_copies_only_the_runtime_allowlist() -> None:
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    # Comments may mention the forbidden form; only executable lines count.
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    assert "COPY . ." not in code, "COPY . . would bake .env, .venv and var/ into the image"
    for needed in (
        "COPY app ./app",
        "COPY alembic ./alembic",
        "COPY scripts ./scripts",
        "COPY alembic.ini ./alembic.ini",
    ):
        assert needed in dockerfile, f"Dockerfile is missing {needed!r}"
