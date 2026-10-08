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
(`demo@admitgraph.local`, created on demand, no password) — this is the
"anonymous demo" mode used by the landing pages and by local development. The
demo user can never sign in by password (`password_hash` is `NULL`).

### Admin access (`/api/v1/admin/*`)

- Accounts whose email appears in **`ADMIN_EMAILS`** (comma-separated
  property, e.g. `ADMIN_EMAILS=alice@example.com,bob@example.com`) are created
  with role `ADMIN` at registration; everyone else is `STUDENT`.
- Admin routes require role `ADMIN`. Authenticated non-admins get `403
  FORBIDDEN`. Anonymous requests are judged by the **demo user's stored role**:
  while the demo user is `ADMIN` the local demo keeps working; in production,
  set `UPDATE users SET role='STUDENT' WHERE email='demo@admitgraph.local'`
  (or run the account through `backend/scripts/grant_demo_admin.py` locally to
  re-grant it) to revoke anonymous admin access.
- `GET /api/v1/auth/me` requires a bearer token and returns `401
  UNAUTHENTICATED` for anonymous callers.

### Required production settings

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET` | Signing key for bearer tokens. **`app/core/security.py` raises at startup if `APP_ENV=production` and `JWT_SECRET` is unset.** Use a long random value (e.g. `openssl rand -hex 32`). |
| `APP_ENV=production` | Enables the `JWT_SECRET` requirement (and other production hardening). |
| `JWT_TTL_SECONDS` | Token lifetime (default 7 days). |
| `ADMIN_EMAILS` | Comma-separated emails granted `ADMIN` on registration. |
| `CORS_ORIGINS` | Must include the frontend origin, comma-separated. |

Rotating `JWT_SECRET` invalidates all outstanding tokens (users must sign in
again).
