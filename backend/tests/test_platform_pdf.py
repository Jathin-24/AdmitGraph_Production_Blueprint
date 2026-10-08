"""PDF export escapes dynamic strings (BACKEND_SPEC §Security: sanitize HTML)."""

from __future__ import annotations

from app.services.export.pdf import _cell, _styles, render_strategy_pdf


def test_cell_escapes_markup_so_program_names_render_literally() -> None:
    style = _styles()["small"]
    paragraph = _cell("Weird <b>Program</b> & Co", style)
    # The text kept its literal characters; '<b>' was NOT parsed as markup.
    assert paragraph.text == "Weird &lt;b&gt;Program&lt;/b&gt; &amp; Co"
    fonts = {fragment.fontName for fragment in paragraph.frags}
    assert "Helvetica-Bold" not in fonts, "escaped '<b>' must not become bold markup"


def test_cell_keeps_the_em_dash_placeholder_for_missing_values() -> None:
    style = _styles()["small"]
    assert _cell(None, style).text == "—"
    assert _cell("", style).text == ""


def test_render_strategy_pdf_survives_hostile_dynamic_strings() -> None:
    payload = {
        "strategy_id": "<script>alert('x')</script>",
        "summary": "Plan A & B <i>plan</i>",
        "plan_health_score": 88,
        "scoring_version": "v1",
        "portfolio": [
            {
                "category": "REACH",
                "priority": 1,
                "program_name": "M.Sc. <b>Adversarial</b> & Sons",
                "institution": "Uni <code>&</code>",
                "rationale": "Fit < 50% & growing",
            }
        ],
        "risks": [
            {
                "severity": "HIGH",
                "title": "Deadline <b>risk</b> & delay",
                "recommended_action": "Confirm & re-check < official site",
            }
        ],
        "roadmap_tasks": [{"title": "Write SoP <draft> & revise", "due_date": "2027-01-15"}],
    }
    data = render_strategy_pdf(payload)
    assert data.startswith(b"%PDF")
