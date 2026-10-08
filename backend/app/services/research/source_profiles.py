"""Curated country -> official study/visa portal routing table.

IMPORTANT: this table is *query-routing configuration only* (it decides which
domain to append as a `site:` operator to a search query). It is NOT product
data: it contains no deadlines, fees, requirements, probabilities or visa rules.
Every domain below is a well-known national study/immigration portal. When no
domain is known the planner falls back to a non-`site:` query (UNKNOWN stays a
valid outcome).

If a country is missing here, queries simply run without a `site:` operator.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialPortals:
    """Official portal domains for one country (any subset may be None)."""

    study_portal: str | None = None  # national study-abroad portal
    government: str | None = None  # official government / immigration domain


# ISO 3166 alpha-2 -> well-known official national portals.
# Used ONLY to build `site:{domain}` query operators.
OFFICIAL_PORTALS: dict[str, OfficialPortals] = {
    "DE": OfficialPortals(study_portal="daad.de", government=None),
    "NL": OfficialPortals(study_portal="nuffic.nl", government=None),
    "AT": OfficialPortals(study_portal="oead.at", government=None),
    "CH": OfficialPortals(study_portal=None, government="admin.ch"),
    "IE": OfficialPortals(study_portal="educationinireland.com", government=None),
    "GB": OfficialPortals(study_portal=None, government="gov.uk"),
    "US": OfficialPortals(study_portal="educationusa.state.gov", government=None),
    "CA": OfficialPortals(study_portal=None, government="canada.ca"),
    "AU": OfficialPortals(study_portal="studyaustralia.gov.au", government=None),
    "FR": OfficialPortals(study_portal="campus-france.org", government=None),
    "DK": OfficialPortals(study_portal="studyindenmark.dk", government=None),
    "SE": OfficialPortals(study_portal="studyinsweden.se", government=None),
    "FI": OfficialPortals(study_portal="studyinfinland.fi", government=None),
    "NO": OfficialPortals(study_portal="studyinnorway.no", government=None),
}


def study_portal(country_code: str | None) -> str | None:
    """National study portal domain for `site:` routing, or None if unknown."""
    if not country_code:
        return None
    return OFFICIAL_PORTALS.get(country_code.upper(), OfficialPortals()).study_portal


def government_portal(country_code: str | None) -> str | None:
    """Official government/immigration domain for `site:` routing, or None."""
    if not country_code:
        return None
    return OFFICIAL_PORTALS.get(country_code.upper(), OfficialPortals()).government
