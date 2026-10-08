"""Mail layer: outbox delivery when disabled, SMTP path, template contracts."""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.services import mail
from app.services.mail import templates

FOOTER = "AdmitGraph — data from cited sources; fit is not an admission probability."


async def test_disabled_mail_writes_outbox_file(monkeypatch, tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(get_settings(), "email_outbox_dir", str(outbox))
    monkeypatch.setattr(get_settings(), "email_enabled", False)

    ok = await mail.send_email(
        "Student One <student@example.com>",
        "Welcome to AdmitGraph",
        "<p>Hi</p>",
        "Hi",
    )

    assert ok is True
    files = sorted(outbox.iterdir())
    assert len(files) == 1
    path = files[0]
    # {timestamp}_{recipient-slug}_{subject-slug}.txt
    assert path.suffix == ".txt"
    parts = path.stem.split("_")
    assert "student-example-com" in path.stem
    assert "welcome-to-admitgraph" in parts
    body = path.read_text(encoding="utf-8")
    assert "To: Student One <student@example.com>" in body
    assert "Subject: Welcome to AdmitGraph" in body
    assert "Hi" in body
    assert "<p>Hi</p>" in body


async def test_send_email_never_raises_on_outbox_error(monkeypatch, tmp_path: Path) -> None:
    # Outbox directory points at a path that cannot be created (a file in the way).
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "email_outbox_dir", str(blocker / "nested"))
    monkeypatch.setattr(get_settings(), "email_enabled", False)

    assert await mail.send_email("a@example.com", "s", "<p>x</p>", "x") is False


async def test_smtp_failure_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_enabled", True)
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("connection refused")

    monkeypatch.setattr(mail.smtplib, "SMTP", boom)

    assert await mail.send_email("a@example.com", "s", "<p>x</p>", "x") is False


async def test_smtp_success_returns_true(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_enabled", True)
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(get_settings(), "smtp_port", 2525)
    monkeypatch.setattr(get_settings(), "smtp_user", "mailer")
    monkeypatch.setattr(get_settings(), "smtp_password", "secret")
    monkeypatch.setattr(get_settings(), "email_from", "noreply@admitgraph.test")
    monkeypatch.setattr(get_settings(), "smtp_starttls", True)

    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            sent["host"] = host
            sent["port"] = port

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> None:
            sent["quit"] = True

        def login(self, user: str, password: str) -> None:
            sent["user"] = user

        def starttls(self) -> None:
            sent["starttls"] = True

        def send_message(self, message: object) -> None:
            sent["message"] = message

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)

    assert await mail.send_email("a@example.com", "Hello", "<p>Hi</p>", "Hi") is True
    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 2525
    assert sent["user"] == "mailer"
    assert sent.get("starttls") is True
    assert sent.get("quit") is True
    message = sent["message"]
    assert message is not None
    raw = message.as_string()  # type: ignore[union-attr]
    assert "<p>Hi</p>" in raw
    assert "a@example.com" in raw
    assert "noreply@admitgraph.test" in raw


def test_welcome_template_brand_and_cta() -> None:
    subject, html, text = templates.welcome_email("Ada", "/onboarding")
    assert subject == "Welcome to AdmitGraph"
    assert 'href="http://localhost:3000/onboarding"' in html
    assert "http://localhost:3000/onboarding" in text
    assert templates.BRAND_BG in html
    assert templates.BRAND_ACCENT in html
    assert templates.BRAND_TEXT in html
    assert FOOTER in html
    assert FOOTER in text
    assert "<html" in html.lower()


def test_all_templates_render_and_link_local_frontend() -> None:
    cases = [
        templates.welcome_email("Ada", "/onboarding"),
        templates.research_complete_email("Ada", "/research"),
        templates.research_failed_email("Ada", "/research", "The provider timed out."),
        templates.change_alert_email(
            "Ada",
            "/monitor",
            "Deadline moved from 15 Jan 2027 to 1 Feb 2027",
        ),
        templates.source_stale_email("Ada", "/monitor", "Example University evidence is stale."),
        templates.roadmap_due_email("Ada", "/dashboard", "Draft SoP is due in 2 days."),
    ]
    for subject, html, text in cases:
        assert subject
        assert "http://localhost:3000/" in html
        assert 'href="http://localhost:3000/' in html
        assert FOOTER in html
        assert FOOTER in text
        assert templates.BRAND_ACCENT in html
        assert templates.BRAND_BG in html
