"""Career signal service: google_jobs query planning + result summarizing.

Job-market signal comes from the google_jobs engine (serpapi_docs "Career"),
never from treating general web results as job-market data. Queries are bounded
by MAX_CAREER_QUERIES per run; planning lives in planner.plan_queries.
"""

from __future__ import annotations

from typing import Any

from app.services.research.planner import MAX_CAREER_QUERIES


def career_summary(search_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize stored google_jobs results for a research run's step output.

    `jobs_found` counts stored job result items; zero results means UNKNOWN
    (no job-market claim is made without evidence).
    """
    jobs = [r for r in search_results if r.get("engine") == "google_jobs"]
    jobs_found = sum(int(r.get("count") or 0) for r in jobs)
    return {
        "career_queries_run": len(jobs),
        "career_queries_budget": MAX_CAREER_QUERIES,
        "jobs_found": jobs_found,
        "engine": "google_jobs",
    }
