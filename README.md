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
```bash
cp .env.example .env

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload

# Frontend
cd frontend
npm ci
npm run dev

# Infra (Postgres + Redis)
docker compose -f deployment/docker-compose.yml up -d postgres redis
```

## Checks
```bash
cd backend && ruff check app tests && mypy app && pytest -q
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```
