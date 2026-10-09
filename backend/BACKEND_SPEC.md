# Backend Implementation Specification

## Structure
backend/
  alembic/                    # migrations (alembic upgrade head)
  app/
    main.py                   # FastAPI app, middleware (request-id, auth,
                              #   rate limit, body cap, security headers), /metrics
    api/
      v1/
        admin.py              # ADMIN-only usage + run logs
        auth.py               # register/login/me + forgot/reset/verify
        documents.py          # checklist + file upload/download/delete
        evidence.py           # list/detail/conflicts/recheck/resolve (scoped)
        health.py             # /health, /health/ready (503 when DB down)
        monitor.py
        notifications.py
        onboarding.py
        profile.py
        programs.py           # catalog + filters + save/unsave
        research.py           # plan/runs/demo/events/cancel (+ run caps)
        strategies.py
    core/
      config.py  errors.py  events.py  limits.py  logging.py
      metrics.py  redis.py  runcontext.py  security.py  throttling.py
    db/
      base.py  models.py  session.py
      repositories/
    schemas/                  # onboarding.py, profile.py, research.py
    services/
      auth/  demo/  documents/  evidence/  export/  llm.py  mail/
      matching/  monitoring/  notifications.py  onboarding.py  profile.py
      research/  risk/  scoring/  serpapi/  strategy/
    workers/                  # locks.py, recovery.py, scheduler.py (in-process)
  scripts/                    # seed_demo, export_schema, export_openapi,
                              # grant_demo_admin, capture_example, fix_fit_explanations
  tests/                      # 614 tests (pytest); scratch DB `admitgraph_test`
  var/                        # gitignored runtime state (file outbox, uploads)

## Layering
Router -> service -> repository -> database.

Never put business logic in routers.
Never put SQL directly in route handlers.
Never call SerpApi from routers.
Never call an LLM directly from frontend code.

## Core service flow
ResearchService.start(profile_id):
1. validate profile
2. normalize profile
3. create research_plan
4. generate bounded query plan
5. dispatch discovery
6. normalize programs
7. select candidates
8. retrieve requirements
9. extract evidence
10. evaluate eligibility
11. calculate fit
12. calculate risks
13. generate portfolio
14. generate roadmap
15. calculate plan health
16. persist strategy_run
17. mark research complete

## Idempotency
A research request should accept an Idempotency-Key.
Hash:
profile snapshot + goal + countries + intake + strategy version.
If same hash is already running, return the existing run.

## Structured LLM
Create an `LLMProvider` interface.
Required method:
`generate_structured(input, schema, model_config)`

All LLM outputs must pass Pydantic validation.

## Matching
Requirement matching is deterministic first.
Examples:
- numeric threshold
- membership
- date comparison
- boolean
- subject/credit match
LLM may normalize subject names or explain ambiguity, but cannot override deterministic facts.

## Risk
Rules should be code/config, not LLM-only.
Examples:
- mandatory requirement NOT_SATISFIED -> CRITICAL
- deadline passed -> CRITICAL
- deadline < 14 days and docs missing -> HIGH
- budget materially below estimated cost -> HIGH
- evidence stale -> MEDIUM
- source conflict -> HIGH unless verified

## Strategy generation
Use deterministic portfolio constraints first, then LLM for explanation.
The LLM may phrase:
- why recommended
- biggest risk
- next action
It may not invent scores or requirements.

## Monitoring
On-demand re-check is MVP.
For a subscription:
1. load previous evidence
2. run bounded fresh search
3. extract current claim
4. compare normalized values
5. create snapshot
6. flag material change
7. attach evidence
8. update freshness

## Security
Status in parentheses — what the code does today.
- Argon2/bcrypt only if local auth is implemented. (Implemented: **Argon2id**
  via `passlib`/`argon2`, HS256 JWT bearer tokens; no OAuth.)
- Prefer managed OAuth if auth is needed. (Not taken — password auth.)
- CORS allowlist. (Implemented.)
- Rate limiting. (Implemented: per-IP/user fixed-window, 60/min default on the
  expensive POSTs, dedicated 5/min per IP **and** per email for
  `POST /auth/forgot` + `POST /auth/verify-request`, per-caller recheck
  cooldown — in-process, so per worker.)
- Request validation. (Pydantic schemas + typed query params.)
- Maximum body size. (11 MB middleware cap → `413 PAYLOAD_TOO_LARGE`; uploads
  additionally capped at 10 MB → `413 FILE_TOO_LARGE`.)
- SSRF protection for any server-side URL fetching.
- Never accept arbitrary URLs for backend fetching without allowlisting and validation.
- Sanitize HTML if rendered. (Backend returns data only; no HTML rendering.)
- Do not store unnecessary personal data.
- Encrypt secrets using platform secret manager.
- Do not log profile PII, tokens, or API keys. (Enforced by
  `tests/test_security.py::test_logs_contain_no_secrets`.)

Also enforced (see `api/API_CONTRACT.md`): security headers on every response
(nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Content-Security-Policy: default-src 'self'`), `401 TOKEN_STALE` after a
password change, 404-not-403 cross-user scoping, admin endpoints
authenticated-ADMIN-only (anonymous included), structured logs that carry
`request_id`/`run_id` but no secrets (asserted by
`test_security.py::test_logs_contain_no_secrets`), and prompt-injection
hardening on LLM inputs (`test_prompt_injection.py` — untrusted web text is
neutralized, delimited and declared untrusted in the system message).

## Reliability
- timeouts on all provider calls
- retry only transient failures
- circuit breaker around provider
- cache normalized search results
- graceful partial strategy
- explicit `PARTIAL` run status
- structured logs with request_id and run_id

## Tests
Status: implemented — **614 tests, 91 % coverage of `backend/app`**; the
plan-by-plan status map (including the remaining gap: no browser E2E) lives in
`../tests/TEST_PLAN.md`.

Unit:
- scoring
- requirement matching
- freshness
- conflict detection
- query planner
- source authority
- risk rules

Integration:
- PostgreSQL repositories
- SerpApi adapter with mocked HTTP
- research orchestration
- API contracts

End-to-end:
- onboarding to strategy
- failed search
- conflicting evidence
- missing IELTS
- insufficient prerequisites
- low budget
- passed deadline
