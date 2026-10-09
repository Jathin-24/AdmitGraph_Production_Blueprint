"""Audit D-3: JWT_SECRET strength guard.

Production must refuse the committed `.env.example` placeholder (and any
secret shorter than 32 chars) instead of silently signing tokens with it;
development only warns, so a copied example config still boots locally.
"""

from __future__ import annotations

import logging

import pytest

from app.core import security
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch: pytest.MonkeyPatch):
    """The dev warning is once-per-process; each test observes its own."""
    monkeypatch.setattr(security, "_weak_secret_warned", False)
    yield


def _configure(monkeypatch: pytest.MonkeyPatch, *, app_env: str, secret: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "jwt_secret", secret)


def test_production_refuses_the_example_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        app_env="production",
        secret=security.EXAMPLE_JWT_SECRET_PLACEHOLDER,
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        security.jwt_secret()


def test_production_refuses_the_placeholder_used_as_a_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        app_env="production",
        secret=security.EXAMPLE_JWT_SECRET_PLACEHOLDER + "-still-the-example",
    )
    with pytest.raises(RuntimeError, match="placeholder"):
        security.jwt_secret()


def test_production_refuses_secrets_shorter_than_32_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, app_env="production", secret="short-but-not-the-example-value")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        security.jwt_secret()


def test_production_accepts_a_strong_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    import secrets

    strong = secrets.token_urlsafe(64)
    _configure(monkeypatch, app_env="production", secret=strong)
    assert security.jwt_secret() == strong


def test_dev_warns_but_boots_with_the_example_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure(
        monkeypatch,
        app_env="development",
        secret=security.EXAMPLE_JWT_SECRET_PLACEHOLDER,
    )
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        assert security.jwt_secret() == security.EXAMPLE_JWT_SECRET_PLACEHOLDER
    assert "JWT_SECRET" in caplog.text
    assert "production" in caplog.text


def test_dev_warns_but_boots_with_a_short_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure(monkeypatch, app_env="development", secret="short-dev-secret")
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        assert security.jwt_secret() == "short-dev-secret"
    assert "JWT_SECRET" in caplog.text


def test_dev_strong_secret_is_silent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import secrets

    strong = secrets.token_urlsafe(64)
    _configure(monkeypatch, app_env="development", secret=strong)
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        assert security.jwt_secret() == strong
    assert "weak" not in caplog.text


def test_dev_warning_is_emitted_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A per-request warning on every decode would drown the logs."""
    _configure(monkeypatch, app_env="development", secret="short-dev-secret")
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        security.jwt_secret()
        security.jwt_secret()
        security.jwt_secret()
    assert caplog.text.count("JWT_SECRET is weak") == 1
