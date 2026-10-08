"""Email templates: simple inline-HTML (no external assets) + plain text.

Brand: background #FAF9F5, accent #1D5C46, text #16181D. Every template has a
CTA to the local frontend (http://localhost:3000{link}) and the honest footer
about what AdmitGraph data means. All templates return ``(subject, html, text)``.
"""

from __future__ import annotations

FRONTEND_BASE = "http://localhost:3000"
FOOTER = "AdmitGraph — data from cited sources; fit is not an admission probability."
BRAND_BG = "#FAF9F5"
BRAND_ACCENT = "#1D5C46"
BRAND_TEXT = "#16181D"


def absolute_url(link: str) -> str:
    """CTA href: local frontend base + in-app route (or an absolute URL as-is)."""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    return f"{FRONTEND_BASE}{link}"


def _render(heading: str, paragraphs: list[str], cta_label: str, cta_url: str) -> tuple[str, str]:
    body_html = "".join(
        f'<p style="margin:0 0 12px;font-size:14px;line-height:1.6;color:{BRAND_TEXT};">{p}</p>'
        for p in paragraphs
    )
    html = f"""<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background-color:{BRAND_BG};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background-color:{BRAND_BG};">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0"
             style="max-width:560px;width:100%;background-color:#FFFFFF;border-radius:8px;">
        <tr><td style="padding:28px 32px;">
          <p style="margin:0 0 16px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;
                    font-weight:bold;color:{BRAND_ACCENT};">AdmitGraph</p>
          <h1 style="margin:0 0 16px;font-size:20px;line-height:1.3;color:{BRAND_TEXT};">{heading}</h1>
          {body_html}
          <p style="margin:24px 0 8px;">
            <a href="{cta_url}"
               style="display:inline-block;background-color:{BRAND_ACCENT};color:#FFFFFF;
                      padding:11px 20px;border-radius:6px;text-decoration:none;font-weight:bold;
                      font-size:14px;">{cta_label}</a>
          </p>
          <p style="margin:8px 0 0;font-size:12px;color:#6B6F76;word-break:break-all;">{cta_url}</p>
          <hr style="border:0;border-top:1px solid #E4E1D9;margin:20px 0;" />
          <p style="margin:0;font-size:12px;line-height:1.5;color:#6B6F76;">{FOOTER}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    lines = [heading, ""]
    lines.extend(paragraphs)
    lines.extend(["", f"Open AdmitGraph: {cta_url}", "", FOOTER])
    return html, "\n".join(lines)


def welcome_email(user_name: str, link: str) -> tuple[str, str, str]:
    subject = "Welcome to AdmitGraph"
    html, text = _render(
        f"Welcome, {user_name}",
        [
            "Your account is ready. Finish onboarding and AdmitGraph will build your first "
            "evidence-backed study-abroad plan.",
            "Every requirement, deadline and cost is traced back to a source you can open.",
        ],
        "Start onboarding",
        absolute_url(link),
    )
    return subject, html, text


def research_complete_email(user_name: str, link: str, summary: str = "") -> tuple[str, str, str]:
    subject = "Your research run is complete"
    paragraphs = [f"Hi {user_name}, your run finished successfully."]
    if summary.strip():
        paragraphs.append(summary.strip())
    paragraphs.append("Your program shortlist, fit scores, risks and roadmap are ready to review.")
    html, text = _render("Your research run is complete", paragraphs, "Review results",
                         absolute_url(link))
    return subject, html, text


def research_failed_email(user_name: str, link: str, detail: str = "") -> tuple[str, str, str]:
    subject = "Your research run could not finish"
    paragraphs = [f"Hi {user_name},"]
    if detail.strip():
        paragraphs.append(detail.strip())
    paragraphs.append(
        "Nothing was invented to fill the gaps — open the research page to adjust your "
        "profile or goal and run it again."
    )
    html, text = _render("Your research run could not finish", paragraphs, "Open research",
                         absolute_url(link))
    return subject, html, text


def change_alert_email(user_name: str, link: str, headline: str) -> tuple[str, str, str]:
    subject = "Monitored program changed"
    html, text = _render(
        "A monitored value changed",
        [f"Hi {user_name},", headline.strip() or "A value you monitor changed.",
         "The change is recorded with the sources it was observed against."],
        "See what changed",
        absolute_url(link),
    )
    return subject, html, text


def source_stale_email(user_name: str, link: str, headline: str) -> tuple[str, str, str]:
    subject = "A source you rely on became stale"
    html, text = _render(
        "A source passed its freshness window",
        [f"Hi {user_name},", headline.strip() or "Stored evidence passed its freshness window.",
         "The stored value may be out of date — re-check the program to look for changes."],
        "Re-check now",
        absolute_url(link),
    )
    return subject, html, text


def roadmap_due_email(user_name: str, link: str, headline: str) -> tuple[str, str, str]:
    subject = "Roadmap task due soon"
    html, text = _render(
        "A roadmap task is due soon",
        [f"Hi {user_name},", headline.strip() or "One of your roadmap tasks is coming due."],
        "Open dashboard",
        absolute_url(link),
    )
    return subject, html, text
