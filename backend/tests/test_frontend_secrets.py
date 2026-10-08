"""TEST_PLAN §Security: "API key not present in frontend build."

Scans the frontend *source* (node_modules/ and .next/ build output are
excluded) for anything that looks like real credential material:

- provider/API key literals (OpenRouter, Groq, Anthropic, OpenAI, Google, AWS),
- full JWT literals,
- `Bearer <key-material>` headers (the benign `Bearer ${token}` template and
  the api.ts comment are explicitly not key material),
- server-side-only environment variables (SERPAPI_API_KEY, JWT_SECRET, ...)
  *resolved* from frontend code (`process.env.X` / `import.meta.env.get("X")`);
  the bare name in user-facing help copy is not a reference,
- `NEXT_PUBLIC_*` variables whose name smells like a secret.

The patterns are proven against planted examples in
`test_secret_patterns_detect_planted_material` so a silent pattern regression
cannot turn this suite green while leaking a key.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"

# Directories that are generated or third-party: never source of truth.
EXCLUDED_DIRS = {"node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo"}

SCANNED_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".css",
    ".html",
    ".md",
    ".env",
    ".txt",
}

# Files without a suffix are still scanned when their name starts with ".env".
ENV_FILE_PREFIX = ".env"

# --- patterns for literal credential material --------------------------------
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openrouter_key": re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{16,}"),
    "groq_key": re.compile(r"\bgsk_[A-Za-z0-9_\-]{20,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),
    "openai_key": re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt_literal": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    # Real header material only: `Bearer eyJ...` or `Bearer gsk_...`, never
    # `Bearer ${token}` (a template) or the api.ts usage comment.
    "bearer_token": re.compile(r"\bBearer\s+(?:eyJ[A-Za-z0-9_\-]|gsk_|sk-|AIza)[A-Za-z0-9_\-\.]{10,}"),
    # A credential-shaped assignment: api_key/secret/token = "<long literal>".
    "assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret|password)"
        r"\b\s*[:=]\s*[\"'][A-Za-z0-9_\-/+=]{24,}[\"']"
    ),
}

# Server-side variables that must never be *read* from frontend code (they are
# backend-only). The bare name in a help/error string is not a leak — only
# resolving the variable in the browser bundle is (TEST_PLAN §Security).
_SERVER_ONLY_NAMES = (
    r"SERPAPI_API_KEY|LLM_API_KEYS?|JWT_SECRET|DATABASE_URL|REDIS_URL|SMTP_PASSWORD"
)
SERVER_ONLY_ENV = re.compile(
    rf"\b(?:(?:process\.env|import\.meta\.env)\.(?:{_SERVER_ONLY_NAMES})"
    rf"|(?:process\.env|import\.meta\.env|Deno\.env\.get)\(\s*[\"'](?:{_SERVER_ONLY_NAMES})[\"']\s*\))"
)
# Bracket access: process.env["SERPAPI_API_KEY"] / import.meta.env["JWT_SECRET"]
SERVER_ONLY_ENV_BRACKET = re.compile(
    r"\b(?:process\.env|import\.meta\.env)\["
    r"[\"'](?:SERPAPI_API_KEY|LLM_API_KEYS?|JWT_SECRET|DATABASE_URL|REDIS_URL|SMTP_PASSWORD)[\"']\]"
)
# The raw name, used only when scanning .env* files (an assignment there would
# ship the credential to the browser).
SERVER_ONLY_ENV_NAME = re.compile(
    r"^(?:SERPAPI_API_KEY|LLM_API_KEYS?|JWT_SECRET|DATABASE_URL|REDIS_URL|SMTP_PASSWORD)$"
)

# NEXT_PUBLIC_* is public by design — unless the name itself smells secret.
SUSPICIOUS_PUBLIC_ENV = re.compile(
    r"\bNEXT_PUBLIC_(?:.*(?:SECRET|PRIVATE|PASSWORD|SERPAPI|JWT|API_KEY))\b"
)

# `process.env.SERPAPI_API_KEY` etc. is caught by SERVER_ONLY_ENV; this just
# documents that plain words ("serpapi" in an error-message check) are benign.
BENIGN_HINT = re.compile(r"includes\(\s*[\"']serpapi[\"']\s*\)")


def _iter_frontend_files(root: Path) -> Iterator[Path]:
    # os.walk (not rglob) so node_modules/.next are never even traversed.
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in sorted(filenames):
            path = Path(current) / filename
            if not path.is_file():
                continue
            if filename.startswith(ENV_FILE_PREFIX) or path.suffix.lower() in SCANNED_SUFFIXES:
                yield path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(FRONTEND_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- tests


def test_frontend_root_exists() -> None:
    assert FRONTEND_ROOT.is_dir(), f"frontend sources not found at {FRONTEND_ROOT}"


def test_frontend_source_contains_no_secret_material() -> None:
    """No credential-shaped literal may appear anywhere in frontend sources."""
    files = list(_iter_frontend_files(FRONTEND_ROOT))
    assert files, "no frontend source files found - the scan would be vacuous"
    findings: list[str] = []
    for path in files:
        text = _read_text(path)
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{_rel(path)}:{line} [{label}] {match.group(0)[:12]!r}...")
    assert not findings, "secret-like material in frontend source:\n" + "\n".join(findings)


def test_frontend_never_references_server_side_secrets() -> None:
    """Backend-only env vars (SerpApi key, JWT secret, DB URL) must not be
    *resolved* from frontend code (TEST_PLAN §Security). Mentioning a variable
    name in user-facing help copy is not a reference and is allowed."""
    findings: list[str] = []
    for path in _iter_frontend_files(FRONTEND_ROOT):
        text = _read_text(path)
        for pattern in (SERVER_ONLY_ENV, SERVER_ONLY_ENV_BRACKET):
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{_rel(path)}:{line} {match.group(0)}")
    assert not findings, "server-side env referenced by frontend:\n" + "\n".join(findings)


def test_frontend_next_public_vars_carry_no_secret_names() -> None:
    """NEXT_PUBLIC_* is inlined into the browser bundle: its name must never
    suggest a secret (keys are resolved server-side only)."""
    findings: list[str] = []
    for path in _iter_frontend_files(FRONTEND_ROOT):
        text = _read_text(path)
        for match in SUSPICIOUS_PUBLIC_ENV.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{_rel(path)}:{line} {match.group(0)}")
    assert not findings, "suspicious NEXT_PUBLIC variable:\n" + "\n".join(findings)


def test_frontend_env_files_contain_no_live_credentials() -> None:
    """Any .env* file in the frontend must be public-only (URLs/flags)."""
    findings: list[str] = []
    for path in _iter_frontend_files(FRONTEND_ROOT):
        if not path.name.startswith(ENV_FILE_PREFIX):
            continue
        for number, line_text in enumerate(_read_text(path).splitlines(), start=1):
            stripped = line_text.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key = stripped.split("=", 1)[0].strip()
            if SERVER_ONLY_ENV_NAME.match(key) or SUSPICIOUS_PUBLIC_ENV.search(key):
                findings.append(f"{_rel(path)}:{number} {key}")
            value = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            if re.fullmatch(r"(?:sk|gsk|AKIA|AIza)[A-Za-z0-9_\-]{16,}", value):
                findings.append(f"{_rel(path)}:{number} key-like value for {key}")
    assert not findings, "credential in frontend env file:\n" + "\n".join(findings)


def test_secret_patterns_detect_planted_material(tmp_path: Path) -> None:
    """Self-check: the pattern library must catch planted secrets, and must
    ignore the benign shapes that exist in the codebase today."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5NXgL03hZEviKKh6QeYU"
    planted = tmp_path / "leak.tsx"
    planted.write_text(
        "\n".join(
            [
                'const a = "sk-or-v1-' + "a" * 32 + '"',
                'const b = "gsk_' + "b" * 32 + '"',
                'const c = "AIza' + "c" * 35 + '"',
                'const d = "' + jwt + '"',
                'const e = "sk-ant-' + "f" * 32 + '"',
                'const g = "sk-proj-' + "g" * 32 + '"',
                'const h = "AKIA' + "H" * 16 + '"',
                'headers: {"Authorization": "Bearer gsk_' + "d" * 32 + '"}',
                'const cfg = {api_key: "' + "e" * 40 + '"}',
                "const leaked = process.env.SERPAPI_API_KEY;",
            ]
        ),
        encoding="utf-8",
    )
    text = _read_text(planted)
    hits = {label for label, pattern in SECRET_PATTERNS.items() if pattern.search(text)}
    assert hits == set(SECRET_PATTERNS), f"patterns not detected: {set(SECRET_PATTERNS) - hits}"
    assert SERVER_ONLY_ENV.search(text), "env access must be detected"
    assert SERVER_ONLY_ENV_BRACKET.search('fetchWith(process.env["JWT_SECRET"])')

    benign = tmp_path / "benign.tsx"
    benign.write_text(
        "\n".join(
            [
                'if (m.includes("serpapi")) return "provider unavailable";',
                'headers: {Authorization: `Bearer ${token}`}',
                "// Bearer token from /auth/login is stored in memory",
                "const base = process.env.NEXT_PUBLIC_API_BASE_URL;",
                'const cfg = {api_key: ""}',
                # User-facing help copy naming the server variable is not a
                # reference: no env lookup, no key material.
                '"configure SERPAPI_API_KEY on the server to re-check claims"',
            ]
        ),
        encoding="utf-8",
    )
    text = _read_text(benign)
    for label, pattern in SECRET_PATTERNS.items():
        assert not pattern.search(text), f"false positive from {label}"
    assert not SERVER_ONLY_ENV.search(text)
    assert not SERVER_ONLY_ENV_BRACKET.search(text)
    assert not SUSPICIOUS_PUBLIC_ENV.search(text)


def test_scanner_skips_node_modules_and_next_build_output() -> None:
    """The scan must exclude generated/dependency trees (they would drown the
    report and are not authored source)."""
    offenders = [
        path
        for path in _iter_frontend_files(FRONTEND_ROOT)
        if any(part in EXCLUDED_DIRS for part in path.parts)
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("label", sorted(SECRET_PATTERNS))
def test_pattern_library_is_non_empty(label: str) -> None:
    assert SECRET_PATTERNS[label].pattern
