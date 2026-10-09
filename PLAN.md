# AdmitGraph — Completion Plan (Gap Elimination Sprint)

**Goal:** close every drawback/gap from the audit (P0-1..5, P1-6..12, P2-13..28), keep the
build green, and ship a submission-ready product for real students.

**Ground rules for every workstream**
1. Do NOT touch files owned by another workstream (ownership table below).
2. Every fix ships WITH a test proving it. No fix without a regression test.
3. Backend gate: `cd backend && ruff check app tests scripts && mypy app && pytest -q` must pass.
   Frontend gate: `cd frontend && npm run lint && npx tsc --noEmit && npm run build` must pass.
4. Never delete or revert uncommitted user work: `frontend/app/components/account.tsx`,
   `frontend/app/providers.tsx`, `frontend/app/components/auth-nudge.tsx`, `frontend/app/lib/auth.tsx`
   are WIP in progress — integrate, do not overwrite.
5. No fabricated/mock data anywhere. UNKNOWN stays a valid state. No admission probabilities.
6. `database/schema.sql` must never diverge from Alembic again (regenerate or document as derived).

---

## Workstream ownership (parallel-safe)

| WS | Lane | Owns (exclusive write access) |
|----|------|-------------------------------|
| **W1** | Backend security P0 | `app/core/security.py`, `app/api/v1/admin.py`, `app/services/profile.py`, `app/core/config.py`, `app/main.py` (rate limit + body cap + headers), `tests/test_security.py`, `tests/integration/test_auth_db.py`, new security tests |
| **W2** | Deploy & ops | `deployment/docker-compose.yml`, `backend/Dockerfile`, new `backend/.dockerignore`, `database/schema.sql`, `backend/alembic/*`, `app/api/v1/health.py`, `app/core/errors.py`, `app/core/logging.py`, `app/workers/*`, `deployment/ENVIRONMENT.md` |
| **W3** | Backend product APIs | `app/api/v1/{programs,auth,documents,evidence,strategies}.py`, `app/services/{auth,documents,evidence}/*`, `app/db/*`, new tests for each |
| **W4** | Frontend core & bugs | `app/lib/api.ts`, `app/lib/schemas.ts`, `app/providers.tsx`, `app/components/*` (incl. `auth-nudge.tsx`, `nav.tsx`), `app/onboarding/page.tsx`, `app/explore/page.tsx`, `app/{login,register,layout,page}.tsx`, new `app/error.tsx` `app/not-found.tsx` `app/loading.tsx`, `next.config.mjs`, `middleware.ts` |
| **W5** | Frontend product pages | `app/dashboard/page.tsx`, `app/programs/[id]/page.tsx`, `app/monitor/page.tsx`, new `app/admin/**`, new `app/reset-password/**`, new `app/profile` sections, new `app/lib/api-extra.ts` |
| **W6** | QA / tooling / docs | frontend test runner (`vitest`), `tests/TEST_PLAN.md`, `README.md`, `docs/MASTER_SPEC.md`, `api/API_CONTRACT.md`, `frontend/README.md`, Prettier, `package.json` scripts, coverage config, repo hygiene (stray files, `.gitignore`) |
| **W7** | Refactor (runs LAST, after W1–W6 merge) | split `app/services/research/orchestrator.py`, `app/services/strategy/persist.py`, move raw `session.execute` out of services into repositories, split `dashboard/page.tsx` |

**Cross-WS contracts (agreed up front so parallel work composes)**

- `GET /programs` new params (W3 implements, W4 consumes):
  `q` (ILIKE on program/university/city), `country`, `degree_level`, `sort=fit_score|name|deadline`,
  plus existing `page`, `page_size`, `saved_only`. Response keeps `{items, total, page, page_size}`.
- `POST /auth/forgot` `{email}` → always 202 `{status:"accepted"}`; `POST /auth/reset` `{token,password}` → 200.
  Email written to existing outbox when SMTP disabled. W3 builds, W5 builds `/reset-password`.
- `POST /documents/{id}/upload` (multipart, ≤10 MB, allowlist pdf/png/jpg/jpeg/docx) → stores under
  `backend/var/uploads`, ownership-scoped; `GET /documents/{id}/file` streams it back. W3 builds, W5 adds UI.
- Evidence health already returns `by_authority`, `unknowns`, `conflicting_count` — W5 consumes, no backend change.
- `roadmap_tasks[].evidence_ids` already in contract — W5 renders "why" links.
- W4 must EXPORT `apiFetch` and `API_BASE` from `app/lib/api.ts` so W5's `api-extra.ts` can import them.

---

## Execution order

```
Phase 1 (parallel):  W1  W2  W3  W4  W5
Phase 2 (after merge): W6  (tests, docs, hygiene, CI hardening)
Phase 3 (last):        W7  (god-file split + layering, guarded by the 580-test suite)
Gate A: backend ruff+mypy+pytest green · frontend lint+tsc+build green
Gate B: audit checklist (below) 100% ticked · README claims match reality
```

## Acceptance checklist (mapped to audit items)

Independent audit verdicts: ✅ verified · ⚠️ partial · 🔄 in progress · ❌ failed

- [x] P0-1 JWT secret fails fast in all envs (W1+W2) — ✅ app-level verified; ✅ compose `${JWT_SECRET:?}` guard + placeholder/short-secret rejection (W9)
- [x] P0-2 admin rejects anonymous; demo user seeded STUDENT; test asserts 403 (W1) — ✅ live-probed 403
- [x] P0-3 onboarding uses authenticated client; contract test scans all `fetch(` (W4) — ✅ enforced by source-scan test
- [x] P0-4 `.dockerignore` + no secrets in image + CI grep gate (W2+W6) — ✅ image inspected
- [x] P0-5 `/health/ready` → 503 when DB down (W2) — ✅ live-probed 503
- [x] P1-6 schema single source of truth + migrate-on-deploy (W2) — ✅ pg_dump-regenerated, CI diff gate
- [x] P1-7 advisory lock on scheduler + global/per-user run concurrency caps (W2+W3) — ✅
- [x] P1-8 daily search/LLM budget with 429 BUDGET_EXCEEDED (W3+W8) — ✅ research + recheck + monitor (W9)
- [x] P1-9 documents endpoints validated (422 not 500) (W3) — ✅
- [x] P1-10 evidence/conflict endpoints ownership-scoped (W3) — ✅ incl. list/conflicts close-out
- [x] P1-11 rate limiter with TTL eviction + streamed body cap (W1) — ✅ live-probed 429/413
- [x] P1-12 generated API types from OpenAPI (W6) — ✅ CI drift gate
- [x] P2-13 explore search + filters, URL-synced (W3+W4) — ✅ UI/API; ✅ pipeline writes country/degree (W9); ✅ demo fixture enriched with DE codes + FK country seeding, live-probed `?country=DE` returns replayed programs (E2E walkthrough)
- [x] P2-14 password reset + email verification (W3+W5+W8) — ✅ incl. /verify-email page
- [x] P2-15 document file upload (W3+W5) — ✅
- [x] P2-16 admin UI (W5) — ✅
- [x] P2-17 evidence health depth + roadmap evidence links (W5) — ✅
- [x] P2-18 responsive mobile nav (W4) — ✅
- [x] P2-19 error/not-found/loading boundaries; strict parse in prod (W4) — ✅
- [x] P2-20 SSR + per-route metadata for landing & program pages (W4+W5) — ✅
- [x] P2-21 httpOnly cookie OR hardened localStorage + CSP/security headers (W1+W4) — ✅ localStorage branch
- [x] P2-22 god files split (W7) — ✅ orchestrator 1291→107, dashboard 1138→209, persist 1417→937; further split of persist.py below ~900 LOC accepted as documented deferral (no user-visible impact)
- [x] P2-23 layering + unit-of-work commits (W7+W11) — ✅ audit-named services + evidence/onboarding/simulator clean (0 raw SQL); research/demo packages explicitly deferred (documented)
- [x] P2-24 frontend test runner + critical-path tests (W6) — ✅ vitest, 30 tests
- [x] P2-25 prompt-injection hardening (W3) — ✅
- [x] P2-26 repo hygiene (W6) — ✅
- [x] P2-27 metrics + request-id on 500s (W2) — ✅ live-probed
- [x] P2-28 README/docs honesty pass (W6+W10) — ✅ 11 false/stale claims fixed post-audit

Submission status: working tree committed (`f3bb80b`, 163 files). A full live
E2E walkthrough afterwards found one demo-path gap — the captured fixture
predated the country-column work, so demo-replayed programs had
`country_code NULL` and `GET /programs?country=DE` returned 0 after the demo
(the primary first-run flow). Fixed by enriching
`backend/scripts/demo/example.json` with the capture's factual country (DE),
seeding the `countries` FK rows in `apply_fixture`, and storing demo
planned-queries via `PlannedQuery.as_dict()` like the live runner; guarded by
integration assertions (fixture countries + `?country=DE` returns replayed
programs) and live-probed end to end.
