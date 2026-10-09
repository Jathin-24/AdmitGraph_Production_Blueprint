"""Scholarship finder service (W13): filters + paging over a verified JSON dataset.

Scholarships are *reference data*, not rows: there is no table, no migration and
no database call anywhere in this module — the endpoint must answer even when
Postgres is down. The dataset lives in ``app/data/scholarships.json`` and is
read once per process; every helper below is pure, so the whole filter/paging
contract is unit-testable without a server or a session.

Honesty contract enforced here (PLAN.md W13):
  * a row's ``source_url`` is the only place its facts may come from;
  * ``deadline``/``amount_text``/``country`` may be ``null`` — a missing figure
    is reported as missing, never filled in;
  * ``page_size`` is hard-capped at 100 no matter what the caller asks for.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "scholarships.json"

#: Enumerations the dataset (and therefore the filters) accept.
DEGREE_LEVELS: tuple[str, ...] = ("bachelors", "masters", "phd")
FUNDING_TYPES: tuple[str, ...] = ("full", "partial", "merit", "need-based")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

#: Keys every row must carry — the W13 acceptance list.
REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "provider",
    "country",
    "degree_levels",
    "funding_type",
    "amount_text",
    "deadline",
    "eligibility",
    "source_url",
)


class Scholarship(TypedDict):
    """One row of the scholarships dataset as served by GET /scholarships."""

    id: str
    name: str
    provider: str
    country: str | None
    degree_levels: list[str]
    funding_type: str
    amount_text: str | None
    deadline: str | None
    eligibility: str
    source_url: str
    details_url: str | None
    last_checked: str


def _normalized_page_size(page_size: int) -> int:
    return max(1, min(page_size, MAX_PAGE_SIZE))


@lru_cache(maxsize=1)
def _raw_dataset() -> dict[str, Any]:
    """Parse and cache the JSON file (one disk read per process)."""
    payload: Any = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{DATASET_PATH.name}: expected a JSON object at the top level")
    return payload


def meta() -> dict[str, Any]:
    """``_meta`` block: last_checked date + the re-verify-at-source note."""
    raw = _raw_dataset().get("_meta")
    return dict(raw) if isinstance(raw, dict) else {}


def load_scholarships() -> list[Scholarship]:
    """Every dataset row, built fresh from the cached payload (never mutated
    in place by callers — the cache is shared for the process lifetime)."""
    raw = _raw_dataset()
    rows = raw.get("scholarships")
    if not isinstance(rows, list):
        raise ValueError(f"{DATASET_PATH.name}: 'scholarships' must be a list")
    last_checked = str(meta().get("last_checked") or "")
    return [_coerce(row, last_checked) for row in rows]


def _coerce(row: Any, last_checked: str) -> Scholarship:
    if not isinstance(row, dict):
        raise ValueError(f"{DATASET_PATH.name}: every scholarship must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in row]
    if missing:
        raise ValueError(f"{DATASET_PATH.name}: row is missing {', '.join(missing)}")
    levels = row["degree_levels"]
    if not isinstance(levels, list):
        raise ValueError(f"{DATASET_PATH.name}: degree_levels must be a list")
    return Scholarship(
        id=str(row["id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),
        country=None if row["country"] is None else str(row["country"]),
        degree_levels=[str(level) for level in levels],
        funding_type=str(row["funding_type"]),
        amount_text=None if row["amount_text"] is None else str(row["amount_text"]),
        deadline=None if row["deadline"] is None else str(row["deadline"]),
        eligibility=str(row["eligibility"]),
        source_url=str(row["source_url"]),
        details_url=None if row.get("details_url") is None else str(row["details_url"]),
        last_checked=last_checked,
    )


def filter_scholarships(
    items: list[Scholarship],
    *,
    q: str = "",
    country: str | None = None,
    degree_level: str | None = None,
    funding_type: str | None = None,
) -> list[Scholarship]:
    """Apply the W13 filter set.

    ``q`` is a case-insensitive substring over name / provider / eligibility
    (the three fields a student scans); ``country``, ``degree_level`` and
    ``funding_type`` are case-insensitive exact matches. A row with
    ``country: null`` (a genuinely multi-country scheme such as Erasmus Mundus)
    matches no country filter — narrowing to a country must not quietly claim
    a destination the source never named. Unknown values simply match nothing.
    """
    needle = q.strip().casefold()
    wanted_country = country.strip().casefold() if country and country.strip() else None
    wanted_level = degree_level.strip().casefold() if degree_level and degree_level.strip() else None
    wanted_funding = (
        funding_type.strip().casefold() if funding_type and funding_type.strip() else None
    )

    matched: list[Scholarship] = []
    for item in items:
        if needle:
            haystack = " ".join((item["name"], item["provider"], item["eligibility"])).casefold()
            if needle not in haystack:
                continue
        if wanted_country is not None:
            row_country = item["country"]
            if row_country is None or row_country.casefold() != wanted_country:
                continue
        if wanted_level is not None and wanted_level not in [
            level.casefold() for level in item["degree_levels"]
        ]:
            continue
        if wanted_funding is not None and item["funding_type"].casefold() != wanted_funding:
            continue
        matched.append(item)
    return matched


def paginate(
    items: list[Scholarship], page: int, page_size: int
) -> tuple[list[Scholarship], int, int, str | None]:
    """Slice ``items`` for one page. Returns (window, total, size, next_cursor).

    ``page`` is floored at 1 and ``page_size`` is capped at MAX_PAGE_SIZE, so a
    caller cannot widen the contract even if it bypasses the route's Query
    validation. ``next_cursor`` mirrors the programs endpoint: the next page
    number as a string, or None on the last page.
    """
    size = _normalized_page_size(page_size)
    current = max(1, page)
    total = len(items)
    start = (current - 1) * size
    window = items[start : start + size]
    has_more = current * size < total
    return window, total, size, (str(current + 1) if has_more else None)


def list_scholarships(
    *,
    q: str = "",
    country: str | None = None,
    degree_level: str | None = None,
    funding_type: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Filter + paginate the dataset and return the programs-shaped envelope.

    Shape is exactly ``{items, page, page_size, total, next_cursor}`` so the
    frontend can reuse the paging UI it already has for programs.
    """
    matched = filter_scholarships(
        load_scholarships(),
        q=q,
        country=country,
        degree_level=degree_level,
        funding_type=funding_type,
    )
    window, total, size, next_cursor = paginate(matched, page, page_size)
    return {
        "items": list(window),
        "page": max(1, page),
        "page_size": size,
        "total": total,
        "next_cursor": next_cursor,
    }
