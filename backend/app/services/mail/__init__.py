"""Outbound email: real SMTP when configured, local file outbox otherwise.

Convention (documented here and asserted by tests/test_mail.py):

* **Disabled** — ``settings.email_enabled`` is False or ``smtp_host`` is
  empty: the message is written to ``settings.email_outbox_dir`` as a
  ``{timestamp}_{recipient}_{subject}.txt`` file (parents created) and
  :func:`send_email` returns ``True`` — it *is* a delivery, to the local
  outbox. Notifications record ``email_status="SKIPPED"`` in this case
  because nothing was actually sent over the wire.
* **Enabled** — SMTP with ``timeout=10`` (starttls + optional login), run via
  ``asyncio.to_thread``. ``True`` → notification status ``SENT``; any failure
  is logged and returns ``False`` (status ``FAILED``).

``send_email`` never raises.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)


def mail_enabled(settings: Settings | None = None) -> bool:
    """True only when a real SMTP send is possible."""
    active = settings if settings is not None else get_settings()
    return bool(active.email_enabled and active.smtp_host)


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "message")[:limit]


def _write_outbox(settings: Settings, to: str, subject: str, html: str, text: str) -> bool:
    try:
        outdir = Path(settings.email_outbox_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        name = f"{stamp}_{_slug(to)}_{_slug(subject)}.txt"
        body = (
            f"From: {settings.email_from}\n"
            f"To: {to}\n"
            f"Subject: {subject}\n"
            f"Date: {datetime.now(UTC).isoformat()}\n\n"
            f"{text}\n\n--- HTML ---\n{html}\n"
        )
        (outdir / name).write_text(body, encoding="utf-8")
        log.info("email written to local outbox: %s", name)
        return True
    except Exception:  # noqa: BLE001 - a failed outbox write must not break callers
        log.exception("local outbox write failed (suppressed)")
        return False


def _send_smtp(settings: Settings, to: str, subject: str, html: str, text: str) -> bool:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_starttls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)
    return True


async def send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Deliver one message; returns False on failure, never raises.

    With mail disabled this writes the message into the local outbox and
    returns True (see module docstring for the status convention).
    """
    settings = get_settings()
    if not mail_enabled(settings):
        return _write_outbox(settings, to, subject, html, text)
    try:
        return await asyncio.to_thread(_send_smtp, settings, to, subject, html, text)
    except Exception:  # noqa: BLE001 - provider failures must never propagate
        log.exception("SMTP delivery failed (suppressed): subject=%r", subject[:80])
        return False
