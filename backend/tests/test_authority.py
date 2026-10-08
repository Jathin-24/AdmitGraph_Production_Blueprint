from app.db.models import SourceAuthority
from app.services.serpapi.authority import classify_domain, is_non_program_domain, looks_like_program_title


def test_official_government() -> None:
    assert classify_domain("www.gov.uk") == SourceAuthority.OFFICIAL_GOVERNMENT
    assert classify_domain("www.bund.de") == SourceAuthority.OFFICIAL_GOVERNMENT


def test_edu_is_official_university() -> None:
    assert classify_domain("cs.stanford.edu") == SourceAuthority.OFFICIAL_UNIVERSITY


def test_news_and_forum() -> None:
    assert classify_domain("www.bbc.com") == SourceAuthority.NEWS
    assert classify_domain("reddit.com") == SourceAuthority.FORUM_SOCIAL
    assert classify_domain("www.linkedin.com") == SourceAuthority.FORUM_SOCIAL


def test_unclassifiable_domain_is_unknown() -> None:
    """TEST_PLAN §Required "No official source -> UNKNOWN": a domain with no
    official/government/accredited signal must classify as UNKNOWN — never be
    quietly upgraded to an official source (unit item 10, source authority)."""
    assert classify_domain("randomsite") == SourceAuthority.UNKNOWN
    assert classify_domain("some-page-host") == SourceAuthority.UNKNOWN
    # A dot alone is credible-secondary at most, still not official.
    assert classify_domain("randomsite.example") == SourceAuthority.CREDIBLE_SECONDARY


def test_official_domain_override() -> None:
    assert classify_domain("www.tug.de", official_domains={"tug.de"}) == SourceAuthority.OFFICIAL_UNIVERSITY


def test_non_program_domains_never_become_programs() -> None:
    # Observed mis-normalizations: document hosts, ranking sites, listicles.
    assert is_non_program_domain("www.scribd.com")
    assert is_non_program_domain("scribd.com")
    assert is_non_program_domain("research.com")
    assert is_non_program_domain("resources.noodle.com")
    assert is_non_program_domain("www.mastersportal.com")
    assert is_non_program_domain("www.yumpu.com")
    # Real program domains — including similar-looking ones — are unaffected.
    assert not is_non_program_domain("tum.de")
    assert not is_non_program_domain("cs.stanford.edu")
    assert not is_non_program_domain("www.researchgate.net")


def test_program_title_gate() -> None:
    # Real program names pass.
    assert looks_like_program_title("Artificial Intelligence (AI) (M.Sc.) | FAU")
    assert looks_like_program_title("Master Artificial Intelligence THI Germany")
    assert looks_like_program_title("M.Sc. Artificial Intelligence - TU Munich")
    assert looks_like_program_title("Computer Science M.Sc.")
    # Guides, rankings and requirement roundups never do.
    assert not looks_like_program_title(
        "Best German Universities to Study Artificial Intelligence 2026"
    )
    assert not looks_like_program_title('Study "Computer science" (Master) in Germany')
    assert not looks_like_program_title(
        "Masters in Computer Science Requirements: GPA 3.0+"
    )
    assert not looks_like_program_title("Artificial Intelligence in Germany: 2027 Master's Guide")
    assert not looks_like_program_title("Timothy Findling")
    assert not looks_like_program_title("")
    assert not looks_like_program_title("   ")
