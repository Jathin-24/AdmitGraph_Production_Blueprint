"""
Program normalization and shortlisting (W7 split from research.orchestrator).

Deterministic discovery-result -> Program upserts, then the shortlist that
feeds the official-site program queries and the reserved scholarship query.

Every Program row is written with the two filter columns the Explore page
consumes (audit D-2: they were never written by the live pipeline, so
``GET /programs?country=DE`` always answered ``total: 0``):

* ``country_code`` — derived from the search step's recorded localization
  (see :func:`_resolve_country_code` for the derivation and its honesty rule);
* ``degree_type``  — derived from the program title by the deterministic
  keyword rule in :func:`derive_degree_type` (NULL when the title does not
  name a degree level).

Both are backfilled on already-normalized rows instead of skipped, so
name-only rows written by earlier runs converge to a filterable state on the
next research run.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Country,
    Program,
    ResearchPlan,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)
from app.services.research.planner import COUNTRY_NAMES
from app.services.serpapi.authority import is_non_program_domain, looks_like_program_title

if TYPE_CHECKING:
    from app.services.research.runner import ResearchService


# --- degree level (audit D-2) -------------------------------------------
# Deterministic title -> canonical degree level. The patterns reuse the same
# degree vocabulary as the title gate in
# ``services.serpapi.authority.looks_like_program_title`` (M.Sc., Master,
# Bachelor, PhD, ...), so anything that passes the gate can be classified —
# and anything that cannot ("diploma", "degree programme") stays NULL rather
# than being guessed. Ordered most-specific first: a title naming both a
# master's and a doctoral level classifies as PHD.
_DEGREE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bph\.?\s?d\b|\bdoctorate\b|\bdoctoral\b|\bdr\.", re.IGNORECASE), "PHD"),
    (
        re.compile(
            r"\bm\.?\s?sc\b|\bmsc\b|\bmasters?\b|\bmba\b|\bm\.?eng\b|\bmeng\b",
            re.IGNORECASE,
        ),
        "MASTERS",
    ),
    (re.compile(r"\bb\.?\s?sc\b|\bbsc\b|\bbachelors?\b", re.IGNORECASE), "BACHELORS"),
)


def derive_degree_type(title: str) -> str | None:
    """Canonical degree level for a program title, or ``None`` when unknown.

    Deterministic keyword rule only — never inferred from anything but the
    title itself (audit D-2): "M.Sc. Artificial Intelligence" -> ``MASTERS``,
    "PhD in Robotics" -> ``PHD``, "Exchange Degree Programme" -> ``None``.
    """
    for pattern, level in _DEGREE_RULES:
        if pattern.search(title):
            return level
    return None


async def _resolve_country_code(session: AsyncSession, code: Any) -> str | None:
    """FK-safe country code for the ``programs.country_code`` column.

    Derivation (audit D-2): the caller passes the country the search was
    localized to — the discovery ``SearchRun.parameters.gl`` the planner
    wrote for the query (the profile's target country), falling back to the
    ``gl`` of the run's SerpApi locale (``search_locale_for``) when a legacy
    or failed run recorded no per-query parameters. That is the profile's
    own target country used on the wire; it is recorded, not guessed.

    Honesty rules:

    * the ``countries`` reference table ships empty (3/3 live programs had
      ``country_code NULL``), so a code whose English name we know factually
      (static ISO -> name map in ``research/planner.COUNTRY_NAMES``) is
      inserted as reference data — required by the FK, and no program fact
      is invented;
    * a code outside that map, a malformed value or a missing ``gl`` yields
      ``None`` and the column stays NULL. NULL beats fabricating a country.

    Domain/TLD data (``serpapi/authority.py``) is deliberately NOT used: a
    ccTLD hints at the institution's website, not at which country the
    localized search (and thus the Explore filter) is about.
    """
    if not isinstance(code, str):
        return None
    normalized: str = code.strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        return None
    name = COUNTRY_NAMES.get(normalized)
    if name is None:
        return None  # cannot name it factually -> leave NULL, never guess
    found = (
        await session.execute(select(Country.code).where(Country.code == normalized))
    ).first()
    if found is not None:
        return normalized
    session.add(Country(code=normalized, name=name))
    await session.flush()
    return normalized


# ------------------------------------------------------------- programs
async def normalize_programs(
    service: ResearchService,
    session: AsyncSession,
    plan: ResearchPlan,
) -> dict[str, Any]:
    # Deterministic normalization of search results into candidate programs.
    # Only program-intent ("discovery") queries feed programs; policy/news/career
    # results are kept as evidence sources but never become programs.
    rows = await session.execute(
        select(SearchResult, SearchRun)
        .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
        .where(SearchRun.engine == "google")
        .where(SearchRun.parameters["purpose"].astext == "discovery")
        .order_by(SearchResult.position.asc().nulls_last(), SearchResult.id.asc())
        .limit(30)
    )
    # Country fallback: the SerpApi locale this run localized its searches
    # with (set per step in ResearchService._execute_step) — used only when
    # an individual SearchRun recorded no `gl` of its own.
    locale = getattr(service, "_search_locale", None)
    locale_country = await _resolve_country_code(
        session, locale.get("gl") if isinstance(locale, dict) else None
    )
    country_cache: dict[str, str | None] = {}

    async def country_for(run: SearchRun) -> str | None:
        params = run.parameters if isinstance(run.parameters, dict) else {}
        raw = params.get("gl")
        if not isinstance(raw, str) or not raw.strip():
            # The run recorded no country of its own (legacy/failed wave):
            # fall back to the locale this run localized its searches with.
            return locale_country
        key = raw.strip().lower()
        if key not in country_cache:
            country_cache[key] = await _resolve_country_code(session, raw)
        # An unusable recorded code stays NULL — no fallback to a country the
        # search did not target (never silently swap one country for another).
        return country_cache[key]

    created = 0
    for item, run in rows.all():
        if not item.title:
            continue
        name = item.title.split(" - ")[0][:200]
        normalized = name.strip().lower()
        if not normalized:
            continue
        # Title intent gate: only results whose title names a degree
        # program (M.Sc., Master, ...) become programs — guides,
        # rankings and requirement roundups stay evidence only.
        if not looks_like_program_title(name):
            continue
        country_code = await country_for(run)
        degree_type = derive_degree_type(name)
        existing = await session.execute(select(Program).where(Program.normalized_name == normalized))
        found = existing.scalar_one_or_none()
        if found is not None:
            # Backfill (audit D-2): rows normalized before the filter columns
            # were written keep their history but gain a country/degree when
            # this run can derive one. Never overwrites an existing value.
            updates: dict[str, Any] = {}
            if found.country_code is None and country_code is not None:
                updates["country_code"] = country_code
            if found.degree_type is None:
                derived = derive_degree_type(found.canonical_name)
                if derived is not None:
                    updates["degree_type"] = derived
            if updates:
                for field, value in updates.items():
                    setattr(found, field, value)
                await session.flush()
            continue
        # Program rows require an institution; create a minimal placeholder institution
        # only when a domain-backed source exists, otherwise skip (UNKNOWN data quality).
        if item.source_id is None:
            continue
        from app.db.models import Institution

        source = await session.get(Source, item.source_id)
        if source is None:
            continue
        # Never normalize news/social/unknown domains into programs; they may
        # still contribute evidence, but they are not study programs.
        if source.source_authority in (
            SourceAuthority.FORUM_SOCIAL,
            SourceAuthority.NEWS,
            SourceAuthority.UNKNOWN,
        ):
            continue
        # Curated non-program platforms (listicles, document hosts, ranking
        # sites) are evidence-worthy at most — never degree programs.
        if source.domain and is_non_program_domain(source.domain):
            continue
        inst_name = (source.domain or name).lower()
        inst = (
            (
                await session.execute(
                    select(Institution)
                    .where(Institution.normalized_name == inst_name)
                    .where(Institution.country_code.is_(None))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if inst is None:
            inst = Institution(
                canonical_name=source.domain or name,
                normalized_name=inst_name,
                domain=source.domain,
            )
            session.add(inst)
            await session.flush()
        # Filter columns (audit D-2): country from the run's search locale,
        # degree level from the title — both NULL when not determinable.
        session.add(
            Program(
                institution_id=inst.id,
                canonical_name=name,
                normalized_name=normalized,
                country_code=country_code,
                degree_type=degree_type,
            )
        )
        created += 1
        if created >= 10:
            break
    await session.commit()

    # Shortlist top programs and run their bounded official-site queries
    # (plan_program_queries is wired into real runs), then spend the
    # remaining funding budget on the reserved site: scholarship query.
    shortlist = await service._shortlist_programs(session, limit=4)
    program_output = await service._run_program_queries(session, plan, shortlist)
    funding_output = await service._run_funding_queries(session, plan, shortlist)
    program_run_ids = list(program_output.pop("search_run_ids", None) or [])
    funding_run_ids = list(funding_output.pop("search_run_ids", None) or [])
    output: dict[str, Any] = {"programs_created": created, **program_output, **funding_output}
    search_run_ids = [*program_run_ids, *funding_run_ids]
    if search_run_ids:
        output["search_run_ids"] = search_run_ids
    return output


async def shortlist_programs(
    session: AsyncSession, limit: int
) -> list[tuple[Program, str | None]]:
    """Rank programs by discovery order weighted by source authority."""
    authority_rank = {
        SourceAuthority.OFFICIAL_UNIVERSITY: 0,
        SourceAuthority.OFFICIAL_GOVERNMENT: 1,
        SourceAuthority.ACCREDITED_BODY: 2,
        SourceAuthority.OFFICIAL_ORGANIZATION: 3,
        SourceAuthority.CREDIBLE_SECONDARY: 4,
    }
    rows = (
        await session.execute(
            select(SearchResult, Source)
            .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
            .join(Source, SearchResult.source_id == Source.id, isouter=True)
            .where(SearchRun.parameters["purpose"].astext == "discovery")
            .order_by(SearchResult.position.asc().nulls_last(), SearchResult.id.asc())
            .limit(60)
        )
    ).all()
    seen: dict[uuid.UUID, tuple[int, Program, str | None]] = {}
    for result, source in rows:
        if not result.title:
            continue
        normalized = result.title.split(" - ")[0].strip().lower()
        if not normalized:
            continue
        program = (
            await session.execute(select(Program).where(Program.normalized_name == normalized))
        ).scalar_one_or_none()
        if program is None:
            continue
        rank = authority_rank.get(source.source_authority if source else SourceAuthority.UNKNOWN, 5)
        if program.id not in seen or rank < seen[program.id][0]:
            domain = source.domain if source else None
            if domain and domain.startswith("www."):
                domain = domain[4:]
            seen[program.id] = (rank, program, domain)
    ranked = sorted(seen.values(), key=lambda t: t[0])[:limit]
    shortlist = [(program, domain) for _rank, program, domain in ranked]
    if not shortlist:
        fallback = (
            await session.execute(
                select(Program).order_by(Program.first_seen_at).limit(limit)
            )
        ).scalars().all()
        from app.db.models import Institution

        for program in fallback:
            institution = await session.get(Institution, program.institution_id)
            shortlist.append((program, institution.domain if institution else None))
    return shortlist
