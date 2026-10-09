"""Audit D-7: notification email links follow settings.frontend_url.

`services/mail/templates.py` used to hardcode http://localhost:3000, so every
CTA in the six notification templates pointed at localhost in a real
deployment. The base now comes from `get_settings().frontend_url` (the same
setting auth.py builds reset/verify links from), with a localhost fallback
only when the setting is unset/blank.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.mail import templates

LOCALHOST = "http://localhost:3000"
PRODUCTION_BASE = "https://app.example.com"


def _notification_templates() -> list[tuple[str, str, str]]:
    """The six templates whose CTA is built by absolute_url()."""
    return [
        templates.welcome_email("Ada", "/onboarding"),
        templates.research_complete_email("Ada", "/research"),
        templates.research_failed_email("Ada", "/research", "The provider timed out."),
        templates.change_alert_email("Ada", "/monitor", "Deadline moved to 1 February."),
        templates.source_stale_email("Ada", "/monitor", "Evidence passed its freshness window."),
        templates.roadmap_due_email("Ada", "/dashboard", "Draft SoP is due in 2 days."),
    ]


def test_absolute_url_uses_the_configured_frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "frontend_url", PRODUCTION_BASE)
    assert templates.absolute_url("/onboarding") == f"{PRODUCTION_BASE}/onboarding"


def test_absolute_url_normalizes_base_and_link_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "frontend_url", f"{PRODUCTION_BASE}/")
    assert templates.absolute_url("dashboard") == f"{PRODUCTION_BASE}/dashboard"


def test_absolute_links_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "frontend_url", PRODUCTION_BASE)
    assert templates.absolute_url("https://other.example/x") == "https://other.example/x"
    assert templates.absolute_url("http://other.example/x") == "http://other.example/x"


def test_blank_frontend_url_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback exists only for an unset/blank setting (local development)."""
    monkeypatch.setattr(get_settings(), "frontend_url", "")
    assert templates.absolute_url("/dashboard") == f"{LOCALHOST}/dashboard"
    monkeypatch.setattr(get_settings(), "frontend_url", "   ")
    assert templates.absolute_url("/dashboard") == f"{LOCALHOST}/dashboard"


def test_every_notification_template_links_to_the_configured_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "frontend_url", PRODUCTION_BASE)
    rendered = _notification_templates()
    assert len(rendered) == 6
    for subject, html, text in rendered:
        assert subject
        assert f'href="{PRODUCTION_BASE}/' in html, subject
        assert f"{PRODUCTION_BASE}/" in text, subject
        assert LOCALHOST not in html, subject
        assert LOCALHOST not in text, subject


def test_default_setting_keeps_local_links_in_local_development() -> None:
    """The dev default (frontend_url=http://localhost:3000) is unchanged."""
    assert get_settings().frontend_url == LOCALHOST
    _subject, html, text = templates.welcome_email("Ada", "/onboarding")
    assert f'href="{LOCALHOST}/onboarding"' in html
    assert f"{LOCALHOST}/onboarding" in text
