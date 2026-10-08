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
- Cache/queue: Redis
- Background jobs: Celery or ARQ; use the simpler implementation that the coding agent can reliably maintain.
- Search: SerpApi
- LLM: structured-output provider behind an internal interface
- Deployment: Vercel frontend + Render/Railway/Fly.io-style backend + managed PostgreSQL/Redis

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

# Configuration: backend/.env (gitignored) — copy the keys from .env.example
#   DATABASE_URL, REDIS_URL, SERPAPI_API_KEY, LLM_* keys
#   JWT_SECRET (required in production), ADMIN_EMAILS
#   EMAIL_ENABLED=false -> emails are written to a local file outbox; set SMTP_* + EMAIL_ENABLED=true to really send
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

Seeding/alternates: tests provision their own scratch database (`admitgraph_test`) automatically and skip when Postgres is down.

## URLs
- App: http://localhost:3000
- API: http://localhost:8000/api/v1 — Swagger: http://localhost:8000/api/v1/docs
- Accounts: register/login at `/register` + `/login` (email + password). Anonymous visitors use the local demo session (`demo@admitgraph.local`, ADMIN). Emails in `ADMIN_EMAILS` become admins on registration.
- Instant demo: research page → "Run the full example" (replays a captured real run; no SerpApi/LLM credits).

## Checks
```bash
cd backend && ruff check app tests scripts && mypy app && pytest -q
cd frontend && npm run lint && npx tsc --noEmit && npm run build   # stop `npm run dev` before build
```
