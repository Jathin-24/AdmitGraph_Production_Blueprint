# Environment Configuration

Backend:
DATABASE_URL=
REDIS_URL=
SERPAPI_API_KEY=
LLM_API_KEY=
LLM_MODEL=
CORS_ORIGINS=
APP_ENV=development
LOG_LEVEL=INFO

Frontend:
NEXT_PUBLIC_API_BASE_URL=

Rules:
- Never use NEXT_PUBLIC_ for secrets.
- `.env` files are ignored by git.
- Provide `.env.example`.
- Production secrets must be managed by deployment platform.
