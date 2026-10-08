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
  shared demo profile (`demo@admitgraph.local`).
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
  bounded on-demand recheck (`409 PROVIDER_UNAVAILABLE` when no SerpApi key is set).
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
- **Admin/observability** — `/admin/search-usage`, `/admin/research-runs`
  (ADMIN-gated), `X-Request-ID` + `request_id` on every error, per-IP rate limits
  on the expensive endpoints and a 1 MB cap on write-request bodies.
- **Demo replay** — a "Run the full example" button (research page and empty-state
  CTAs on dashboard/explore) replays the captured run in seconds.

Backend test suite: **300+ tests** (unit, contract, and DB integration).

## Repository
See:
- `docs/MASTER_SPEC.md`
- `database/schema.sql`
- `api/API_CONTRACT.md`
- `backend/BACKEND_SPEC.md`
- `frontend/FRONTEND_SPEC.md`
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

alembic upgrade head                 # apply schema (required on first run)
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
- API: http://localhost:8000/api/v1 — Swagger: http://localhost:8000/api/v1/docs
- Accounts: register/login at `/register` + `/login` (email + password). Anonymous visitors use the local demo session (`demo@admitgraph.local`, ADMIN). Emails in `ADMIN_EMAILS` become admins on registration.
- Instant demo: research page → "Run the full example" (replays a captured real run; no SerpApi/LLM credits).

## Checks
```bash
cd backend && ruff check app tests scripts && mypy app && pytest -q   # 300+ tests; DB-backed ones use the scratch DB
cd frontend && npm run lint && npx tsc --noEmit && npm run build   # stop `npm run dev` before build
```
