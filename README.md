# AdmitGraph — Production Build Blueprint

AdmitGraph is a live, evidence-backed study-abroad strategy engine.

## Product promise
Don't just find universities. Find out whether the plan will actually work — and what can make it fail.

## Build philosophy
- Narrow, deep, reliable MVP.
- Germany + MSc is the demo scenario; the schema and services remain country-agnostic.
- SerpApi is a core live-information dependency, not a decorative integration.
- No invented admission requirements, deadlines, fees, visa rules, or probabilities.
- Every consequential dynamic claim must have evidence, authority, retrieval timestamp, freshness, and conflict status.
- Fit Score is an explainable product score, never an admission probability.
- UNKNOWN is a valid state and must never be silently converted to a guess.

## Stack
- Frontend: Next.js + React + TypeScript + Tailwind CSS
- Backend: Python + FastAPI + Pydantic + SQLAlchemy 2 + Alembic
- Database: PostgreSQL
- Cache: Redis (SerpApi search cache; falls back to in-memory when Redis is unreachable)
- Background jobs: in-process asyncio tasks + an in-process scheduler started with the app (startup recovery of interrupted runs, monitor checks, evidence freshness sweep, roadmap reminders) — no external worker or queue
- Search: SerpApi
- LLM: structured-output provider behind an internal interface
- Deployment: Vercel frontend + Render/Railway/Fly.io-style backend + managed PostgreSQL/Redis

## Current state

What is implemented today (verified in `backend/app`, `frontend/app`):

- **Multi-student auth** — register/login/`GET /auth/me`, Argon2id passwords, HS256
  JWT bearer tokens, `ADMIN_EMAILS` role grant; anonymous traffic falls back to the
  shared demo profile (`demo@admitgraph.local`, a STUDENT account — admin routes
  stay closed to it). Password reset (`POST /auth/forgot` + `POST /auth/reset`) and
  email verification (`POST /auth/verify-request` + `POST /auth/verify`) are
  implemented and emailed through the same SMTP-or-file-outbox path, and the
  `/verify-email` page consumes the emailed `?token=` link (verify automatically
  on arrival, plus a resend when signed in).
- **Program search** — `GET /programs` with text search (`q` over program /
  university / city), `country` + `degree_level` filters, `saved_only`,
  `sort` (`fit_score` | `name` | `deadline`) and paging, plus detail,
  requirements, per-program evidence and save/unsave endpoints (the listing UI
  is `/explore`).
- **Onboarding wizard** — backend-driven schema (`GET /onboarding/schema`), typed
  answer coercion, progress tracking; optional English test type/date/expiry and
  `subjects` (strings or `{name, credits}` objects), with legacy payloads keeping
  their old behaviour.
- **Research pipeline** — plan/run creation with `Idempotency-Key`, SerpApi searches
  localized to the profile's target country (`hl`/`gl`/`location`), per-step events,
  cancel, startup recovery, and `POST /research/demo` (replays a captured run —
  zero SerpApi/LLM spend).
- **Evidence** — provenance, source authority, freshness deadlines, conflict
  detection plus manual resolution (`POST /evidence/conflicts/{id}/resolve`), and a
  bounded on-demand recheck (error order `404` → `429 RATE_LIMITED` on the
  cooldown → `409 PROVIDER_UNAVAILABLE` when no SerpApi key is set). Evidence reads
  are ownership-scoped: an item is visible only when it is attached to the caller's
  profile or belongs to the built-in demo corpus — otherwise `404`.
- **Documents + file uploads** — document records with a file endpoint
  (`POST /documents/{id}/upload` → `GET /documents/{id}/file`), 413 on oversized
  bodies, 422 on validation failures.
- **Strategy** — persisted fit dimensions and explainable scoring, risks with
  acknowledge/resolve/dismiss transitions, application portfolio cards (fit score,
  top-2 reasons, worst open risk, cost band, evidence freshness, next
  deadline/action), roadmap tasks, evidence health, PDF export, and a
  counterfactual simulator (`TOP_3_REJECTED`, `BUDGET_MINUS_25_PERCENT`,
  `IELTS_LOWERED`, `REMOVE_COUNTRY` with an explicit `modifications.country`,
  `DEADLINE_MISSED`, `CUSTOM`).
- **Monitoring + scheduler** — subscriptions over five field keys with manual and
  scheduler-driven checks, materiality filtering and plain-language explanations;
  the same tick sweeps stale evidence and sends roadmap reminders.
- **Notifications + mail** — inbox (`{items, unread_count}`), mark-read/read-all,
  listeners on research completion and registration; email goes through SMTP when
  configured, otherwise to a local file outbox at `backend/var/outbox`, so nothing
  leaves the machine by default.
- **Admin/observability** — `/admin/search-usage`, `/admin/research-runs` plus a
  `/admin` UI, all ADMIN-gated (both anonymous and non-admin callers get `403`);
  `X-Request-ID` + `request_id` on every error; per-IP rate limits on the
  expensive endpoints (including a dedicated forgot/verify-request budget) and an
  ~11 MB cap on request bodies; `GET /metrics` at the API root;
  `GET /health/ready` answers `503` until the database is reachable;
  security headers on every response (`X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Content-Security-Policy: default-src 'self'`).
  Spend is bounded by those rate limits, the per-user research run caps
  (`429 RESOURCE_EXHAUSTED`) and a daily search budget counted over
  `search_runs` rows per UTC day (`AGRAPH_DAILY_SEARCH_BUDGET`, default 500,
  `0` = unlimited; `AGRAPH_DAILY_SEARCH_BUDGET_PER_USER` per account) —
  `429 BUDGET_EXCEEDED` from `POST /research/plan` and `POST /research/runs`
  once the budget is spent.
- **Frontend hardening** — responsive nav with an accessible mobile disclosure,
  a root `error.tsx` + `not-found.tsx` boundary (with `loading.tsx` skeletons on
  the eight data-heavy routes: root, admin, dashboard, explore, monitor,
  notifications, programs/[id], research), and every authenticated call going
  through `lib/api.ts` so the bearer token is never dropped (enforced by a
  source-scan test).
- **Demo replay** — the research page's "Run the full example" button replays
  the captured run in seconds; explore, notifications and program detail offer
  empty-state "Run the full example" CTAs and dashboard/landing offer "See a
  live example first", all of which link to `/research` to start it.

Backend test suite: **580 tests** (unit, contract, and DB integration), **91 %
  line coverage** of `backend/app`. Frontend: **30 tests** (Vitest + Testing
  Library — API helpers, zod schemas, nav/filters components, retry policy,
  source-scan and OpenAPI-contract checks).

Derived artefacts are checked for staleness in CI: `database/schema.sql`
(Alembic), `api/openapi.json` (FastAPI) and
`frontend/app/lib/api-types.generated.ts` (`npm run gen:api-types`).

## Repository
See:
- `docs/MASTER_SPEC.md`
- `database/schema.sql`
- `api/API_CONTRACT.md` (+ `api/openapi.json`, the machine-readable spec)
- `backend/BACKEND_SPEC.md`
- `frontend/FRONTEND_SPEC.md` + `frontend/README.md`
- `serpapi_docs/SERPAPI_INTEGRATION.md`
- `prompts/OPENCODE_MASTER_PROMPT.md`
- `tests/TEST_PLAN.md`

## Local setup

Infra first (Postgres on host port **5433**, Redis on 6379):
```bash
docker compose -f deployment/docker-compose.yml up -d postgres redis
```

Backend:
```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows (source .venv/bin/activate on macOS/Linux)
pip install -e ".[dev]"

# Configuration: backend/.env (gitignored) — variable list in deployment/ENVIRONMENT.md
#   DATABASE_URL, REDIS_URL, SERPAPI_API_KEY, LLM_* keys
#   JWT_SECRET (required in production), ADMIN_EMAILS
#   EMAIL_ENABLED=false -> emails are written to a local file outbox (backend/var/outbox); set SMTP_* + EMAIL_ENABLED=true to really send
#   SCHEDULER_ENABLED=true -> automatic monitor checks / freshness / reminders

alembic upgrade head                 # apply schema (required on first run
                                     # bare-metal; `docker compose up` runs this
                                     # automatically via the one-shot `migrate` service)
python -m scripts.seed_demo          # optional: demo persona + fixtures

uvicorn app.main:app --port 8000
```

Frontend:
```bash
cd frontend
npm ci
npm run dev                          # http://localhost:3000
```

Seeding/alternates: tests provision their own scratch database (`admitgraph_test`)
automatically and skip when Postgres is down. Override that scratch URL with
`AGRAPH_TEST_DATABASE_URL` — never point it at your real `DATABASE_URL`.

## URLs
- App: http://localhost:3000
- API: http://localhost:8000/api/v1 — Swagger: http://localhost:8000/api/v1/docs — spec: http://localhost:8000/api/v1/openapi.json (committed copy: `api/openapi.json`)
- Main pages: `/` (landing), `/dashboard`, `/explore`, `/research`, `/monitor`, `/notifications`, `/profile`, `/onboarding`, `/programs/[id]` (detail), `/admin`
- Accounts: register/login at `/register` + `/login` (email + password). Anonymous visitors use the local demo session (`demo@admitgraph.local`, STUDENT — it cannot reach `/admin`). Password recovery: `/forgot-password` → `/reset-password`. Emails in `ADMIN_EMAILS` become admins on registration; an admin account is required for the `/admin` UI.
- Instant demo: research page → "Run the full example" (replays a captured real run; no SerpApi/LLM credits).
- Metrics (Prometheus text): http://localhost:8000/metrics

## Checks
```bash
# Backend — ruff, mypy, then the suite (580 tests, 91% app coverage; DB-backed
# tests provision their own scratch DB and skip when Postgres is down)
cd backend && ruff check app tests scripts && mypy app && pytest -q
# One-off coverage report (not enforced as a threshold in CI):
#   pytest -q --cov=app --cov-report=term-missing

# Frontend — lint, unit tests, build, typecheck (order matters: `next build`
# generates the gitignored next-env.d.ts that tsc needs; stop `npm run dev`
# before building)
cd frontend && npm run lint && npm test && npm run build && npx tsc --noEmit

# Derived artefacts — regenerate and commit when the API/schema changes:
#   cd backend && python -m scripts.export_schema --url <db-url>   # database/schema.sql
#   cd backend && python -m scripts.export_openapi                 # api/openapi.json
#   cd frontend && npm run gen:api-types                           # api-types.generated.ts
```
