"""Deterministic source-authority classification by domain heuristics."""

from __future__ import annotations

import re

from app.db.models import SourceAuthority

OFFICIAL_GOVERNMENT_TLDS = (".gov", ".gov.uk", ".gc.ca", ".gov.au", ".gov.in", ".bund.de")
NEWS_DOMAINS = {
    "reuters.com", "bbc.com", "bbc.co.uk", "thehindu.com",
    "timesofindia.indiatimes.com", "cnn.com",
}
FORUM_DOMAINS = {
    "reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com",
    "facebook.com", "twitter.com", "x.com",
}
ACCREDITED_HINTS = {"naac", "aicte", "daad", "wgac", "studyprogram.de", "anabin"}

_UNIVERSITY_HINTS = re.compile(r"(\.edu\b|\.ac\.[a-z]{2}\b|university|hochschule|universitaet|universite)")


def classify_domain(domain: str, *, official_domains: set[str] | None = None) -> SourceAuthority:
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    if official_domains and any(d == od or d.endswith("." + od) for od in official_domains):
        return SourceAuthority.OFFICIAL_UNIVERSITY
    if any(d == tld.lstrip(".") or d.endswith(tld) for tld in OFFICIAL_GOVERNMENT_TLDS):
        return SourceAuthority.OFFICIAL_GOVERNMENT
    if d in NEWS_DOMAINS or any(d.endswith("." + n) for n in NEWS_DOMAINS):
        return SourceAuthority.NEWS
    if d in FORUM_DOMAINS or any(d.endswith("." + n) for n in FORUM_DOMAINS):
        return SourceAuthority.FORUM_SOCIAL
    if any(h in d for h in ACCREDITED_HINTS):
        return SourceAuthority.ACCREDITED_BODY
    if ".edu" in d or ".ac." in d or _UNIVERSITY_HINTS.search(d):
        return SourceAuthority.OFFICIAL_UNIVERSITY
    if d.count(".") >= 1:
        return SourceAuthority.CREDIBLE_SECONDARY
    return SourceAuthority.UNKNOWN
