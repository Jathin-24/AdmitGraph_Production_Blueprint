# OpenCode Master Build Prompt — AdmitGraph

You are the principal engineer responsible for implementing AdmitGraph from this repository specification.

## Absolute rules
1. Read every file under `docs/`, `database/`, `api/`, `frontend/`, `backend/`, `serpapi_docs/`, `tests/` before coding.
2. Do not invent product requirements.
3. Do not replace PostgreSQL with SQLite.
4. Do not call SerpApi from the frontend.
5. Do not expose API keys.
6. Do not create fake evidence.
7. Do not invent admissions requirements, tuition, deadlines, visa rules, scholarships, or job statistics.
8. UNKNOWN is valid.
9. Do not use admission probability.
10. Every consequential dynamic claim must have evidence metadata.
11. All LLM outputs must be schema-validated.
12. Business logic belongs in services, not route handlers.
13. Use migrations.
14. Write tests for every scoring and matching rule.
15. Do not build unrequested giant features before the MVP is stable.

## Goal
Build a production-grade web application for first-time international students.

The core loop is:
PROFILE → DISCOVER → VERIFY → MATCH → RISK-CHECK → PLAN → MONITOR

## Required implementation
### Phase 1
- repo setup
- Next.js frontend
- FastAPI backend
- PostgreSQL
- Redis
- Alembic
- environment configuration
- lint/typecheck/test
- Docker Compose for local development
- CI

### Phase 2
Implement all tables in `database/schema.sql` as SQLAlchemy models and Alembic migrations.

### Phase 3
Implement typed SerpApi adapter:
- google
- google_jobs
- google_news
Optional:
- scholar
- trends
- maps

### Phase 4
Implement onboarding and profile APIs.

### Phase 5
Implement research orchestration.

### Phase 6
Implement evidence extraction and conflict detection.

### Phase 7
Implement deterministic requirement matching and fit scoring.

### Phase 8
Implement risk engine.

### Phase 9
Implement strategy portfolio and roadmap.

### Phase 10
Implement frontend dashboard and evidence UX.

### Phase 11
Implement failure simulator and on-demand monitoring.

### Phase 12
Production hardening.

## Coding style
Python:
- type hints
- async where I/O-bound
- Pydantic schemas
- SQLAlchemy 2 style
- clear dependency injection
- no hidden global state

TypeScript:
- strict mode
- no `any` unless justified
- typed API responses
- reusable components
- accessibility

## Research orchestration contract
Research is asynchronous.

POST `/api/v1/research/runs`:
- validates profile
- creates research plan
- returns run ID
- background worker executes plan

Frontend subscribes to progress.

## Evidence contract
Every extracted claim must point to:
- source
- search result
- retrieval timestamp
- confidence
- extraction version

## Score contract
Score must be reproducible from:
- profile snapshot
- requirement values
- scoring weights
- scoring version

Persist all of these.

## Demo
Seed a safe synthetic demo student. Do not hard-code fake university facts. Demo data may reference stored verified evidence fixtures, clearly labeled as demo fixtures.

The UI must demonstrate:
1. live SerpApi research
2. program portfolio
3. eligibility matrix
4. a visible risk
5. evidence chain
6. failure simulation
7. change detection

## Completion criteria
Do not declare complete until:
- migrations run from empty DB
- API starts from clean environment
- frontend starts from clean environment
- tests pass
- no secrets are committed
- OpenAPI is generated
- README has exact setup commands
- error states work
- research failures do not crash the UI
- evidence cannot be displayed without provenance
