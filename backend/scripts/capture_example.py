"""Capture a real completed research run into the demo replay fixture.

Reads the MAIN database (settings.database_url — never the test database) and
writes scripts/demo/example.json: the institutions, programs, sources,
evidence, requirements, intakes and planned queries of an already-completed
real run. The demo endpoint replays this fixture; nothing is invented here.

Run: python scripts/capture_example.py [--run-id <uuid>] [--out <path>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    Evidence,
    Institution,
    Intake,
    Program,
    Requirement,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    Source,
)
from app.db.session import get_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUT = BACKEND_DIR / "scripts" / "demo" / "example.json"
DEFAULT_RUN_ID = "a23e2e3b-a9e6-48c4-9a28-dc12d77c1157"

META_NOTE = "Captured from a real AdmitGraph research run — real cited sources"


def _encode(value: Any) -> Any:
    """JSON-safe encoding: Decimal/UUID/datetime/enum -> primitives."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


async def build_fixture(run_id: UUID) -> dict[str, Any]:
    """Read the completed run's artifacts from the main database."""
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not configured")
    engine = get_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        plan = await session.get(ResearchPlan, run_id)
        if plan is None:
            raise SystemExit(f"Research run {run_id} not found in the database")
        if plan.status != RunStatus.SUCCEEDED:
            raise SystemExit(f"Research run {run_id} is {plan.status.value}, not SUCCEEDED")
        steps = (
            await session.execute(
                select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == run_id)
            )
        ).scalars().all()
        step_outputs = {
            s.step_key: {"status": s.status.value, "output": _encode(s.output)} for s in steps
        }

        institutions = (await session.execute(select(Institution))).scalars().all()
        programs = (await session.execute(select(Program))).scalars().all()
        sources = (await session.execute(select(Source))).scalars().all()
        evidence = (await session.execute(select(Evidence))).scalars().all()
        requirements = (await session.execute(select(Requirement))).scalars().all()
        intakes = (await session.execute(select(Intake))).scalars().all()

        source_by_id = {s.id: s for s in sources}
        program_ids = {p.id for p in programs}

        meta = {
            "captured_at": datetime.now(UTC).isoformat(),
            "source_run_id": str(run_id),
            "note": META_NOTE,
            "requested_goal": _encode(plan.requested_goal),
        }
        payload: dict[str, Any] = {
            "meta": meta,
            "planned_queries": _encode(plan.planned_queries or []),
            "step_outputs": step_outputs,
            "institutions": [
                {
                    "id": str(i.id),
                    "canonical_name": i.canonical_name,
                    "normalized_name": i.normalized_name,
                    "country_code": i.country_code,
                    "city": i.city,
                    "website_url": i.website_url,
                    "domain": i.domain,
                    "institution_type": i.institution_type,
                    "authority_score": _encode(i.authority_score),
                }
                for i in institutions
            ],
            "programs": [
                {
                    "id": str(p.id),
                    "institution_ref": str(p.institution_id),
                    "canonical_name": p.canonical_name,
                    "normalized_name": p.normalized_name,
                    "degree_type": p.degree_type,
                    "field_of_study": p.field_of_study,
                    "specialization": p.specialization,
                    "city": p.city,
                    "country_code": p.country_code,
                    "language": p.language,
                    "official_url": p.official_url,
                    "duration_months": p.duration_months,
                    "tuition_amount": _encode(p.tuition_amount),
                    "tuition_currency": p.tuition_currency,
                    "extra": _encode(p.extra),
                    "last_verified_at": _encode(p.last_verified_at),
                    "active": p.active,
                }
                for p in programs
            ],
            "sources": [
                {
                    "id": str(s.id),
                    "url": s.url,
                    "canonical_url": s.canonical_url,
                    "domain": s.domain,
                    "title": s.title,
                    "source_authority": s.source_authority.value,
                    "publisher": s.publisher,
                    "country_code": s.country_code,
                    "source_type": s.source_type,
                }
                for s in sources
            ],
            "evidence": [],
            "requirements": [],
            "intakes": [],
        }

        evidence_rows: list[dict[str, Any]] = []
        for e in evidence:
            src = source_by_id.get(e.source_id)
            subject_ref = (
                str(e.subject_id)
                if e.subject_id is not None and e.subject_id in program_ids
                else None
            )
            evidence_rows.append(
                {
                    "id": str(e.id),
                    "source_ref": str(e.source_id),
                    "claim_type": e.claim_type,
                    "subject_type": e.subject_type,
                    "subject_ref": subject_ref,
                    "claim": e.claim,
                    "normalized_claim": e.normalized_claim,
                    "snippet": e.snippet,
                    "summary": e.summary,
                    "extracted_value": _encode(e.extracted_value),
                    "authority_score": _encode(e.authority_score),
                    "confidence": e.confidence.value,
                    "status": e.status.value,
                    "retrieved_at": _encode(e.retrieved_at),
                    "published_at": _encode(e.published_at),
                    "freshness_deadline": _encode(e.freshness_deadline),
                    "content_hash": e.content_hash,
                    "extraction_model": e.extraction_model,
                    "extraction_version": e.extraction_version,
                    # Provenance of the claim, denormalized for display:
                    "source_url": src.canonical_url if src else None,
                    "source_domain": src.domain if src else None,
                    "source_authority": src.source_authority.value if src else None,
                }
            )
        payload["evidence"] = evidence_rows

        payload["requirements"] = [
            {
                "id": str(r.id),
                "program_ref": str(r.program_id),
                "requirement_type": r.requirement_type,
                "title": r.title,
                "normalized_key": r.normalized_key,
                "operator": r.operator,
                "value": _encode(r.value),
                "mandatory": r.mandatory,
                "applies_to": _encode(r.applies_to),
                "status": r.status.value,
                "last_verified_at": _encode(r.last_verified_at),
            }
            for r in requirements
        ]
        payload["intakes"] = [
            {
                "id": str(i.id),
                "program_ref": str(i.program_id),
                "intake_label": i.intake_label,
                "intake_year": i.intake_year,
                "start_date": _encode(i.start_date),
                "application_deadline": _encode(i.application_deadline),
                "deadline_type": i.deadline_type,
                "status": i.status,
            }
            for i in intakes
        ]

    return payload


def write_fixture(payload: dict[str, Any], out_path: Path) -> dict[str, Any]:
    """Write the captured payload to disk (sync helper, called from main)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "institutions": len(payload["institutions"]),
        "programs": len(payload["programs"]),
        "sources": len(payload["sources"]),
        "evidence": len(payload["evidence"]),
        "requirements": len(payload["requirements"]),
        "intakes": len(payload["intakes"]),
        "bytes": out_path.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID, help="completed research run id")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output json path")
    args = parser.parse_args()
    payload = asyncio.run(build_fixture(UUID(args.run_id)))
    out_path = Path(args.out)
    stats = write_fixture(payload, out_path)
    print(f"Wrote {out_path}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
