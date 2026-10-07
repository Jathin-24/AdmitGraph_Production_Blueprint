from app.db.models import SourceAuthority
from app.services.serpapi.authority import classify_domain


def test_official_government() -> None:
    assert classify_domain("www.gov.uk") == SourceAuthority.OFFICIAL_GOVERNMENT
    assert classify_domain("www.bund.de") == SourceAuthority.OFFICIAL_GOVERNMENT


def test_edu_is_official_university() -> None:
    assert classify_domain("cs.stanford.edu") == SourceAuthority.OFFICIAL_UNIVERSITY


def test_news_and_forum() -> None:
    assert classify_domain("www.bbc.com") == SourceAuthority.NEWS
    assert classify_domain("reddit.com") == SourceAuthority.FORUM_SOCIAL


def test_official_domain_override() -> None:
    assert classify_domain("www.tug.de", official_domains={"tug.de"}) == SourceAuthority.OFFICIAL_UNIVERSITY
