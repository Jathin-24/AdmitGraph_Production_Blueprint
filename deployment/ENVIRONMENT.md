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
JWT_SECRET=

Frontend:
NEXT_PUBLIC_API_BASE_URL=

Rules:
- Never use NEXT_PUBLIC_ for secrets.
- `.env` files are ignored by git.
- Provide `.env.example`.
- Production secrets must be managed by deployment platform.

## `APP_ENV`

`APP_ENV` selects the runtime profile (`development` by default, see
`app/core/config.py`):

- `development` (default): local development and the compose stack as shipped.
- `production`: **fails closed at startup** — `app/core/security.py` raises
  `RuntimeError` if `JWT_SECRET` is unset or empty, so a production deploy
  cannot silently fall back to an auto-generated secret (audit P0-1). Set it
  explicitly on every replica; two replicas with different secrets issue
  tokens the other rejects.

Generate a secret (never commit it, never reuse the dev value):

```
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Rotating `JWT_SECRET` invalidates all outstanding tokens (users must sign in
again).

## Authentication

AdmitGraph uses **local email + password accounts** (no OAuth provider):

- **Passwords** are hashed with **Argon2id** (`app/core/security.py`); plaintext
  passwords are never stored or logged. Minimum length is 8 characters.
- **Sessions** are **HS256 JWT bearer tokens** minted at
  `POST /api/v1/auth/register` and `POST /api/v1/auth/login`, sent as
  `Authorization: Bearer <token>`, and decoded into per-request context by the
  middleware in `backend/app/main.py` (invalid/expired token → `401` error
  envelope; the frontend then clears the stored token).
- **Frontend storage**: the token is kept in `localStorage` under
  `TOKEN_KEY = "admitgraph_token"` (`frontend/app/lib/api.ts`); `getToken()` /
  `setToken()` read and write it. Any `401` response automatically clears it,
  so a stale session degrades to the anonymous demo session instead of erroring
  forever.

### Anonymous demo session fallback

Requests **without** a bearer token operate on the stable local demo account
(`demo@admitgraph.local`, created on demand, no password) for **profile and
demo-content routes only** — this is the "anonymous demo" mode used by the
landing pages and by local development. The demo user can never sign in by
password (`password_hash` is `NULL`).

### Admin access (`/api/v1/admin/*`)

- Accounts whose email appears in **`ADMIN_EMAILS`** (comma-separated
  property, e.g. `ADMIN_EMAILS=alice@example.com,bob@example.com`) are created
  with role `ADMIN` at registration; everyone else is `STUDENT`.
- Admin routes require an authenticated role of `ADMIN`. **Anonymous requests
  (no bearer token) are always `403 FORBIDDEN`** — there is no demo-role
  fallback and no way to grant anonymous admin access
  (`backend/app/api/v1/admin.py`). Authenticated non-admins also get
  `403 FORBIDDEN`.
- `GET /api/v1/auth/me` requires a bearer token and returns `401
  UNAUTHENTICATED` for anonymous callers.

## Migrations (compose `migrate` service)

`deployment/docker-compose.yml` runs a one-shot **`migrate`** service
(`alembic upgrade head`) after Postgres becomes healthy, and the `backend`
service starts only once it exits successfully
(`depends_on: condition: service_completed_successfully`). Schema changes are
therefore never skipped or raced on deploy (audit P1-6).

`database/schema.sql` is a **derived artefact** — regenerate it, never
hand-edit it (PLAN ground rule 6):

```
cd backend
python -m scripts.export_schema --url <scratch-db-url>
# pg_dump not on PATH (e.g. Docker Desktop):
python -m scripts.export_schema --url <scratch-db-url> \
    --pg-dump-cmd "docker exec -e PGPASSWORD=... <container> pg_dump -U <user> -h localhost -d <db>"
```

It runs the migrations against a scratch database, dumps the schema, and
appends the alembic head stamp row so `psql -f database/schema.sql` on a fresh
database leaves `alembic upgrade head` as a no-op.

## Health probes

- `GET /api/v1/health` — liveness: `200 {"status":"ok"}`, never touches the DB.
- `GET /api/v1/health/ready` — readiness: `200 {"status":"ready"}` only when a
  DB ping succeeds; **`503 {"status":"unavailable"}` when the database does not
  answer** (audit P0-5). Point load balancers / compose healthchecks at
  `/health/ready`, not `/health` — a 503 means "do not send traffic here".

## Observability

- `GET /metrics` — Prometheus exposition (text format,
  `prometheus_client.CONTENT_TYPE_LATEST`), no auth; bind it behind your
  scrape network, not the public internet. Exposes request counts/latency
  (`path`, `method`, `status` labels), in-flight runs, and run-cap gauges.
- Every response (including errors) carries an **`X-Request-ID`** header; the
  incoming value is honoured if the client sends one. Unhandled 500s are logged
  with that `request_id` and the JSON error envelope carries it too, so a user
  screenshot and the server logs always correlate (audit P1-9).

## Request limits

- **Body size**: requests larger than **`MAX_BODY_BYTES = 11,000,000`**
  (~11 MB, `backend/app/main.py`) are rejected with `413` before the handler
  runs, whether or not `Content-Length` is declared.
- **Rate limits**: one bucket per authenticated user; anonymous callers share
  the IP bucket.
- **Research run concurrency** (`app/core/limits.py`): at most
  `AGRAPH_MAX_GLOBAL_RUNS` (default 4) runs at once per process and
  `AGRAPH_MAX_RUNS_PER_USER` (default 2) concurrent runs per student. The
  **global** cap queues — the run waits for a free slot rather than failing.
  The **per-user** cap does **not** queue: `POST /research/plan` and
  `POST /research/runs` peek first and answer `429 RESOURCE_EXHAUSTED` with an
  actionable message when the caller already has `AGRAPH_MAX_RUNS_PER_USER`
  runs in progress (`backend/app/api/v1/research.py`), instead of creating a
  plan that would sit `QUEUED` indefinitely.

## Required production settings

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET` | Signing key for bearer tokens. **`app/core/security.py` raises at startup if `APP_ENV=production` and `JWT_SECRET` is unset.** Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `APP_ENV=production` | Enables the `JWT_SECRET` requirement (and other production hardening). |
| `JWT_TTL_SECONDS` | Token lifetime (default 7 days). |
| `ADMIN_EMAILS` | Comma-separated emails granted `ADMIN` on registration. |
| `CORS_ORIGINS` | Must include the frontend origin, comma-separated. |
| `FRONTEND_URL` | Frontend origin used to build links in outgoing emails (welcome, research, monitor alerts, password reset / email verification) — `services/mail/templates.py` and `api/v1/auth.py` both read `settings.frontend_url` (default `http://localhost:3000`, dev only). Set it to the URL users actually open, e.g. `https://app.example.com`. |
| `DATABASE_URL` / `REDIS_URL` | Postgres and Redis endpoints (the compose stack wires these automatically). |
| `SCHEDULER_ENABLED` | Background tick loop (default `true`); ticks are cross-replica safe via a PostgreSQL advisory lock, so multiple replicas may run it. |
