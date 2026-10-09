# AdmitGraph Test Plan

Status legend: **✅ implemented** — tests exist today; **🟡 partial** — covered
in a weaker form than the plan imagined (noted inline); **⛔ not implemented** —
no code, no test.

Verification date: 2026-10. Backend: **614 tests passing, 91 % line coverage of
`backend/app`** (`pytest -q --cov=app --cov-report=term-missing`). Frontend: **74
tests** (`cd frontend && npm test`, Vitest + Testing Library). CI runs ruff,
mypy, the full backend suite, a frontend source scan, the OpenAPI/path contract
checks and staleness diffs for every derived artefact.

## Unit tests
All sixteen live under `backend/tests/` (mostly `test_matching_scoring.py`,
`test_fit_scoring.py`, `test_risk.py`, `test_strategy.py`, `test_simulator.py`,
`test_evidence.py`). **✅ implemented**

1. CGPA normalization — `test_matching_scoring.py`, `test_onboarding_wizard.py`.
2. Percentage normalization — `test_matching_scoring.py`.
3. Language score threshold — `test_ielts_threshold_partial`,
   `test_missing_ielts_is_unknown_not_guessed`,
   `test_reported_non_ielts_needs_verification_never_conversion`.
4. Test expiry — `test_expiry_is_checked_before_the_missing_score`, `test_risk.py`.
5. Prerequisite subject matching — `test_matching_scoring.py`, `test_risk.py`.
6. Credit matching — `test_credits_below_threshold_fail`,
   `test_matching_scoring.py`.
7. Deadline comparison — `test_risk.py`, `test_matching_scoring.py`,
   `test_api_shapes.py`.
8. Budget comparison — `test_budget_threshold`, `test_fit_scoring.py`.
9. Evidence freshness — `test_evidence.py`, `test_authority.py`.
10. Source authority — `test_authority.py`.
11. Evidence conflict — `test_evidence.py` (detection, resolution, scoping).
12. Fit score calculation — `test_fit_scoring.py`.
13. Risk severity — `test_risk.py`.
14. Portfolio category — `test_strategy.py`, `test_api_shapes.py`.
15. Portfolio diversification — `test_strategy.py`.
16. Counterfactual scenario transformations — `test_simulator.py`,
    `test_simulation_monitoring.py`.

## Required test cases

### Complete profile
Expected: strategy generated with evidence. **✅** — `test_strategy_api.py`,
`test_demo.py` (end-to-end replay of a captured run).

### Missing IELTS
Expected: language risk; no invented score. **✅** —
`test_missing_ielts_is_unknown_not_guessed`, `test_risk.py`.

### Insufficient prerequisite
Expected: critical/high risk and verification action. **✅** —
`test_matching_scoring.py`, `test_risk.py`.

### Conflicting deadline
Expected: conflict visible; confidence downgraded. **✅** — `test_evidence.py`
(detection + resolution), `test_risk.py`.

### No official source
Expected: UNKNOWN. **✅** — `test_authority.py`, `test_evidence.py`
(`UNKNOWN` authority/status stays UNKNOWN, never a guess).

### Low budget
Expected: financial risk and alternatives. **✅** — `test_budget_threshold`,
`test_risk.py`, `test_strategy.py`.

### Deadline passed
Expected: program excluded or marked unavailable for selected intake. **✅** —
`test_planner.py`, `test_risk.py` (DEADLINE risks), `test_simulator.py`
(`DEADLINE_MISSED`).

### All top programs risky
Expected: broaden strategy. **✅** — `test_strategy.py` portfolio-category +
diversification cases, `test_risk.py`.

### SerpApi timeout
Expected: partial/cached evidence if valid; no UI crash. **🟡 partial** —
`test_serpapi.py` + `test_research_reliability.py` cover provider errors,
retries and degraded runs server-side (timeout, HTTP errors, empty/invalid
payloads). "No UI crash" is asserted by the frontend's error-boundary/route
behaviour rather than an automated browser test — there is **no
Playwright/Cypress suite**.

### LLM malformed JSON
Expected: validation retry then safe failure. **✅** — `test_llm_extraction.py`
(extraction/validation retry, safe failure), `test_research_reliability.py`.

### Provider rate/credit error
Expected: friendly error and cached evidence where valid. **✅ server-side** —
`test_security.py` (429 envelope), `test_throttling.py`, `test_research_reliability.py`.
Note: the daily `429 BUDGET_EXCEEDED` search budget (plan item P1-8) is now
**implemented and tested** — `test_budget.py` (helpers + settings defaults/env)
and `integration/test_research_budget_db.py` (429 at the budget on both run
endpoints, admission below it, `0` = unlimited, only-today's-rows counting,
per-user isolation). The other enforced spend bounds remain the per-IP rate
limits and the per-user concurrent-run cap (`429 RESOURCE_EXHAUSTED`,
`test_limits.py`).

### Duplicate program
Expected: canonicalization prevents duplicate program records. **✅** —
`test_matching_scoring.py` canonicalization cases.

### Stale evidence
Expected: visible stale state and recheck option. **✅** — `test_evidence.py`
(freshness deadlines, recheck + its 404 → 429 → 409 error order),
`test_scheduler.py` (freshness sweep).

### Monitoring change
Expected: snapshot and material-change alert. **✅** — `test_monitoring.py`,
`test_platform_monitor_api.py`.

## Security tests
**✅ all implemented**

- API key not present in frontend build — `backend/tests/test_frontend_secrets.py`
  (source scan of `frontend/` + env files) **and**
  `frontend/app/source-scan.test.ts` (no credential-shaped literals, no bare
  `fetch(` outside the auth-attaching clients).
- CORS blocks unknown origin — `test_security.py::test_cors_unknown_origin_blocked`.
- rate limit expensive endpoints — `test_security.py` (plan/recheck/GET-provider
  paths, per-user buckets, counter eviction), `test_session_hardening.py`
  (forgot/verify-request 5/min per IP and per target email).
- malformed UUID rejected — `test_security.py::test_malformed_uuid_rejected`.
- oversized payload rejected — `test_security.py` (middleware cap, chunked
  bodies, understated Content-Length) + `FILE_TOO_LARGE` upload tests.
- arbitrary URL fetching blocked — `test_security.py::test_no_ssrf_or_cross_user_url_params`,
  `test_prompt_injection.py` (prompt-injection hardening).
- logs contain no secret — `test_security.py::test_logs_contain_no_secrets`,
  `test_platform_logging.py`.
- authorization prevents cross-user profile access — `test_api_shapes.py`,
  `test_strategy_api.py`, `test_evidence.py` (404-not-403 scoping for runs,
  strategies, subscriptions, documents, notifications, evidence),
  `test_applications_api.py` (foreign row 404 on PATCH **and** DELETE, absent
  from the other user's list, owner's row unchanged after both attempts),
  `test_session_hardening.py` (TOKEN_STALE).

Not in the original plan but added: security-header assertions, JWT
key-derivation/forgery, production fail-closed without `JWT_SECRET`, Docker
image hygiene (`test_image_hygiene.py`), readiness `503` (`test_health_ready.py`),
metrics (`test_metrics.py`).

## Contract tests

- **✅** API path contract — `backend/tests/test_contract.py` extracts the route
  table from the FastAPI app and asserts the paths the clients depend on (it
  asserts **path extraction**, not generated schemas).
- **✅** OpenAPI spec freshness — `api/openapi.json` is exported by
  `backend/scripts/export_openapi.py`; CI regenerates it and diffs.
- **✅** Frontend generated types freshness — `npm run gen:api-types`
  (`openapi-typescript`) writes `frontend/app/lib/api-types.generated.ts`; CI
  diffs it, and `frontend/app/lib/api-contract.test.ts` asserts the committed
  spec, the generated `paths` type and the endpoints the client calls all agree.
  (This is the implemented form of "OpenAPI schemas match frontend client
  types".)
- **✅** `database/schema.sql` freshness — CI regenerates it from a
  freshly-migrated database and diffs (`test_export_schema.py` locally).

## W12-W15 submission wave (2026-10-09)

- **✅ Application tracker** — `backend/tests/test_applications_api.py`
  (10: round-trip CRUD, partial patch semantics, `?status=` filtering, 422 on
  unknown status / blank university / unknown filter, cross-user 404 on read
  and mutate, delete-then-delete 404) + `frontend/app/lib/applications-api.test.ts`
  (8: status grouping order, counts, label mapping, list URL).
- **✅ Scholarship finder** — `backend/tests/test_scholarships.py` (23, no DB:
  dataset integrity — every row has a real `source_url` and `last_checked` —
  plus filter/pagination/envelope behaviour) +
  `frontend/app/lib/scholarships-api.test.ts` (14: query/URL helpers, deadline
  and snippet formatting).
- **✅ Visa & funding checklist** — `frontend/app/visa/visa.test.ts` (21:
  localStorage key/round-trip/corrupt-storage, completion math, country-data
  integrity — official-host allowlist, figure-implies-source, ISO last-checked).
- **✅ Wider catalog** — `integration/test_demo_db.py` (fixture replay +
  idempotency + `test_demo_fixture_catalog_is_multi_country_and_officially_sourced`:
  10–14 programs, ≥3 countries, every URL-carrying program backed by an
  `OFFICIAL_UNIVERSITY` source), pins in `tests/test_demo.py` (9 / 11 / 348 / 82).

## Frontend test plan (P2-24, implemented)

Runner: Vitest + jsdom + Testing Library (`cd frontend && npm test`).

- `app/lib/api.test.ts` — `apiFetch` URL building, auth header, error envelope
  → typed error, retry/backoff behaviour.
- `app/lib/schemas.test.ts` — zod response schemas accept real payloads and
  reject malformed ones.
- `app/providers-retry.test.tsx` — React Query retry policy (no retry on 4xx).
- `app/components/nav.test.tsx` — nav rendering, accessible mobile disclosure
  (`aria-expanded`/`aria-controls`), account menu wiring.
- `app/explore/filters-url.test.tsx` — explore filters ↔ URL sync (query, page,
  country selection resets paging).
- `app/source-scan.test.ts` — no bare `fetch(` outside `lib/api.ts` /
  `lib/api-extra.ts` in client code, no credential-shaped literals.
- `app/lib/api-contract.test.ts` — spec ↔ generated types ↔ client endpoints.
- `app/lib/applications-api.test.ts` — tracker grouping/counts/URL helpers.
- `app/lib/scholarships-api.test.ts` — scholarship query/URL/format helpers.
- `app/visa/visa.test.ts` — visa checklist persistence + dataset integrity.

Not covered (documented gaps): no browser/E2E suite (Playwright/Cypress), no
visual regression tests, no accessibility audit beyond the ARIA assertions in
the nav test.
