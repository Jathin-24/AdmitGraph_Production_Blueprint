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
    "facebook.com", "twitter.com", "x.com", "linkedin.com",
}
ACCREDITED_HINTS = {"naac", "aicte", "daad", "wgac", "studyprogram.de", "anabin"}

# Document hosts, ranking sites and listicles: pages *about* programs that must
# never be normalized into Program rows. They may still contribute evidence as
# secondary sources. Domains observed mis-normalizing in production discovery.
NON_PROGRAM_DOMAINS = {
    "scribd.com",
    "slideshare.net",
    "issuu.com",
    "coursehero.com",
    "research.com",
    "noodle.com",
    "yumpu.com",
    "mastersportal.com",
    "studying-in-germany.org",
    "admit-lab.com",
}


def is_non_program_domain(domain: str) -> bool:
    """True for curated non-program platforms (match is on registrable domain)."""
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    return any(d == nd or d.endswith("." + nd) for nd in NON_PROGRAM_DOMAINS)


# A program is *named* by its degree (M.Sc., Master, Bachelor, ...); guides and
# roundups are named by their listicle framing. Deterministic, documented rules
# so junk pages cannot become programs (MASTER_SPEC §9 source hierarchy).
_PROGRAM_DEGREE_TOKEN = re.compile(
    r"m\.?sc\.?|msc|b\.?sc\.?|bsc|master'?s?\b|bachelor'?s?\b|mba|meng|m\.?eng"
    r"|ph\.?d|doctorate|diploma|\bdegree\b",
    re.IGNORECASE,
)
_PROGRAM_JUNK_MARKERS = re.compile(
    r"\bguide\b|\bbest\b|\btop\b|\brank(ing|ings|ed)?\b|\brequirements?\b"
    r"|\beverything\b|\bhow to\b|\bfaq\b|\bcatalog\b|\bhandbook\b|\bexplained\b"
    r"|\byou need\b",
    re.IGNORECASE,
)


def looks_like_program_title(title: str) -> bool:
    """True when a result title names an actual degree program.

    - must contain a degree token (M.Sc., Master, Bachelor, ...),
    - must not contain a listicle/guide marker (guide, best, rankings, ...),
    - must not start with the verb "study" (DAAD field guides read
      `Study "Computer science" (Master) in Germany`).
    """
    t = title.strip()
    if not t or t.lower().startswith("study "):
        return False
    if not _PROGRAM_DEGREE_TOKEN.search(t):
        return False
    return _PROGRAM_JUNK_MARKERS.search(t) is None

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
