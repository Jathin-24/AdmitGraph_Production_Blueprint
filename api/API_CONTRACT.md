# AdmitGraph API Contract

Base URL: `/api/v1`

All JSON errors:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable message",
    "details": {},
    "request_id": "uuid"
  }
}
```

Every response — success or error — carries `X-Request-ID` (echoed from the request
header when the client sends one, generated otherwise) and `X-Response-Time-Ms`.
`401 UNAUTHENTICATED` (bad/expired bearer token), `413 PAYLOAD_TOO_LARGE` and
`429 RATE_LIMITED` are produced by middleware but use this same envelope, so
`error.request_id` is always present and equals the `X-Request-ID` header.

## Health
GET `/health`
GET `/health/ready`

## Auth
POST `/auth/register` — body `{email, password, full_name?}` → 201 `{token, user}`
POST `/auth/login` — body `{email, password}` → 200 `{token, user}`
GET `/auth/me` → 200 `{user}` (requires `Authorization: Bearer <token>`)

`user`: `{"id": "uuid", "email": "...", "full_name": "..."|null, "role": "STUDENT"|"ADMIN"}`.

Errors: `400 VALIDATION_ERROR` (missing/invalid email, password shorter than 8
characters), `409 EMAIL_TAKEN`, `401 INVALID_CREDENTIALS` on login (never reveals
whether the email exists), `401 UNAUTHENTICATED` on `/auth/me` without a valid token.
Without any token, requests run as the local anonymous demo profile; an invalid or
expired token is a 401 so clients drop the stale session instead of writing to the
wrong account.

## Profile
GET `/me/profile`
POST `/me/profile`
PATCH `/me/profile`
GET `/me/profile/completion`
POST `/me/profile/validate`

## Onboarding
GET `/onboarding/schema`
POST `/onboarding/answers`
GET `/onboarding/progress`

The onboarding schema must be backend-driven so frontend fields can change without rewriting business logic.

POST `/onboarding/answers` body: `{"answers": {key: value, ...}}` →
`{"accepted_keys": ["..."]}`. Values arrive as form strings; the backend coerces
them to column types (a bad number is `400 VALIDATION_ERROR`, never a 500).

Besides the schema keys, `answers` accepts these optional companions:

- `english_test_type` — which test the score belongs to. Stored with canonical
  casing for the known set `english_overall | IELTS | IELTS_ACADEMIC | TOEFL | PTE |
  DET`; any other value is stored exactly as reported (never guessed).
- `english_test_date`, `english_expiry_date` — `YYYY-MM-DD` (blank clears).
- `subjects` — array of strings (`["Math", "Physics"]`, or comma text) **or**
  objects `[{"name": "Math", "credits": "4"}]`. Replaces the subjects onboarding
  owns on the profile's current education record; blank names dropped,
  case-insensitive duplicates collapsed, negative credits → 400.

Legacy compatibility: payloads without these keys behave exactly as before —
the score alone keeps writing/updating the legacy `english_overall` row, date
companions only apply when their key is present (a plain score update never
wipes earlier dates), and a payload that introduces a real test type adopts the
legacy row instead of duplicating it. Subjects are only touched when the
`subjects` key is present.

GET `/onboarding/progress` → `{completion_percent, answered_keys,
missing_required_keys}`. `missing_required_keys` is still decided only by the
original required answers — `field_of_study`, `current_degree`, `cgpa`,
`total_budget_amount`; optional answers (including the companions above) only
raise `completion_percent`.

## Research
POST `/research/plan`
POST `/research/runs`
POST `/research/demo`
GET `/research/runs/{run_id}`
GET `/research/runs/{run_id}/events`
POST `/research/runs/{run_id}/cancel`

POST `/research/plan` returns:
```json
{
  "research_plan_id": "uuid",
  "status": "QUEUED"
}
```

Both `POST /research/plan` and `POST /research/runs` take the same body
(`{goal: {...}, intake_year?}`), dispatch the plan in the background as soon as it
is `QUEUED`, and honour the same optional **`Idempotency-Key`** header: the key is
hashed with the caller's profile id, and while a plan created with that digest is
still `QUEUED`/`RUNNING` the same plan is returned instead of a new one.

`POST /research/demo` replays a captured real run for the caller's profile
(the shared demo profile for anonymous traffic — zero SerpApi/LLM spend) and
returns the same `{research_plan_id, status}` shape. While a demo run is still
queued/running, a repeat POST returns that same in-flight run. Errors: `503 DEMO_FIXTURE_INVALID` when the
captured fixture (`backend/scripts/demo/example.json`) is missing or unusable.

`GET /research/runs/{run_id}` →
```json
{
  "id": "uuid",
  "status": "QUEUED",
  "mode": "live",
  "planned_queries": ["..."],
  "error_message": null,
  "created_at": "ISO-8601",
  "started_at": null,
  "completed_at": null
}
```
`mode` is `"live"` or `"demo"` so the UI can badge replays.
`GET .../events` → `{run_id, steps: [{step_key, service_name, status, error_message, output}]}`.
`POST .../cancel` → `{status}` (cancels only while `QUEUED`/`RUNNING`).

Runs are scoped to the caller's profile: another profile's run id (or an unknown
one) is `404 NOT_FOUND`.

The UI should use polling or Server-Sent Events for progress. Do not make the browser call SerpApi directly.

## Programs
GET `/programs`
GET `/programs/{program_id}`
GET `/programs/{program_id}/requirements`
GET `/programs/{program_id}/evidence`
POST `/programs/{program_id}/save`
DELETE `/programs/{program_id}/save`

## Strategy
GET `/strategies`
GET `/strategies/{strategy_id}`
GET `/strategies/{strategy_id}/portfolio`
GET `/strategies/{strategy_id}/risks`
PATCH `/strategies/{strategy_id}/risks/{risk_id}`
GET `/strategies/{strategy_id}/roadmap`
GET `/strategies/{strategy_id}/evidence-health`
GET `/strategies/{strategy_id}/fit`

GET `/strategies` → `{"items": [{"id", "status", "plan_health_score", "summary", "created_at"}]}`
where `plan_health_score` is a **string or null** (decimal stringified), same as
the detail endpoint. `GET /strategies/{strategy_id}` returns those fields plus
`scoring_version`, `strategy_version`, and the embedded `portfolio`, `risks`
and `roadmap_tasks` arrays. `GET /strategies/{strategy_id}/roadmap` →
`{"items": [{id, title, task_type, status, due_date, evidence_ids}]}` with `due_date` as
`YYYY-MM-DD` or null; `evidence_ids` is a (possibly empty) array of evidence
UUID strings backing the task.

### Portfolio row (`GET /strategies/{id}/portfolio` → `{"items": [...]}`, and the
`portfolio` array of `GET /strategies/{id}`) — exact keys produced by
`_portfolio_rows`:

```json
{
  "program_id": "uuid-string",
  "program_name": "M.Sc. Computer Science",
  "institution": "Technical University of Munich",
  "category": "LOWER_RISK",
  "priority": 1,
  "rationale": "why this program is in the plan",
  "fit_score": "72.500",
  "reasons": ["Academic 42/100", "Language 25/100"],
  "top_risk": {"id": "uuid-string", "severity": "HIGH", "risk_type": "DEADLINE", "title": "..."},
  "estimated_cost": {"currency": "EUR", "amount": 12000.0, "band": "MEDIUM"},
  "next_deadline": "2027-01-15",
  "next_action": "Submit TU application",
  "evidence_freshness": {"status": "FRESH", "stale": 0, "total": 4}
}
```

- `program_id`: string uuid; `program_name`, `institution`: string or null.
- `category`: `LOWER_RISK | TARGET | REACH`; `priority`: integer; `rationale`: string.
- `fit_score`: **string or null** (the plan's linked fit assessment, else the
  profile's latest fit for that program; null when neither exists).
- `reasons`: array of **up to 2 strings**; when the plan stores none it falls back
  to the top-2 stored dimension reasons (`"Academic 42/100"` style), `[]` if the
  fit has no stored subscores. Never fabricated.
- `top_risk`: object `{id, severity, risk_type, title}` or null — the worst **OPEN**
  program-level risk for this program (portfolio-level worries are not attached).
- `estimated_cost`: object written at build time as
  `{currency, amount, band}` where `band` is `LOW | MEDIUM | HIGH | null`
  (null when tuition or its currency is unknown); `{}` when tuition was never
  extracted — no invented currency, amount or band.
- `next_deadline`: `YYYY-MM-DD` or null; `next_action`: string or null.
- `evidence_freshness`: object, always present (never null):
  `{status: "FRESH" | "STALE" | "UNKNOWN", stale: <int>, total: <int>}` —
  `UNKNOWN` when the program has no evidence at all, `STALE` when any claim is
  marked stale or past its freshness deadline.

### Risks (`GET /strategies/{id}/risks` → `{"items": [...]}`, sorted by severity)

Row keys: `id`, `risk_type` (string token such as `DEADLINE`, `FINANCIAL`,
`TEST`, `LANGUAGE`, `PREREQUISITE`, `ELIGIBILITY`, `VISA_COMPLIANCE`,
`DOCUMENT`, `CAREER`, `DATA_QUALITY`, `SOURCE_CONFLICT`,
`INFORMATION_FRESHNESS` — not a closed enum), `severity`
(`CRITICAL | HIGH | MEDIUM | LOW`),
`title`, `reason`, `recommended_action`, `status`
(`OPEN | ACKNOWLEDGED | RESOLVED | DISMISSED`), `confidence`
(`HIGH | MEDIUM | LOW`), `program_id` (string|null), `requirement_id`
(string|null), `resolved_at` (ISO-8601|null).

`PATCH /strategies/{strategy_id}/risks/{risk_id}` — body:
```json
{ "status": "ACKNOWLEDGED" }
```
`status` must be exactly one of `ACKNOWLEDGED`, `RESOLVED`, `DISMISSED`
(`OPEN` is never a target — the risk engine re-derives it on the next run).
Returns `{"risk": {<risk row>}}`. `RESOLVED` stamps `resolved_at`; every other
transition clears it. Errors: `422 VALIDATION_ERROR` for any other status value,
`404 NOT_FOUND` when the risk is unknown or belongs to another profile.

### Evidence health (`GET /strategies/{id}/evidence-health`)

```json
{
  "programs_total": 0,
  "programs_with_evidence": 0,
  "evidence_total": 0,
  "by_status": {},
  "by_confidence": {},
  "by_authority": {},
  "stale_count": 0,
  "unknowns": 0,
  "conflicting_count": 0
}
```
`by_authority` counts evidence per source authority
(`OFFICIAL_UNIVERSITY | OFFICIAL_GOVERNMENT | OFFICIAL_ORGANIZATION |
ACCREDITED_BODY | CREDIBLE_SECONDARY | NEWS | FORUM_SOCIAL | UNKNOWN`);
`unknowns` counts evidence rows whose status is `UNAVAILABLE`;
`conflicting_count` is `by_status["CONFLICTING"]` (0 when absent).

### Fit (`GET /strategies/{id}/fit`)

`{"scoring_version": "...", "items": [{"program_id", "overall_score", "explanation"}]}`
with `overall_score` as a string.

### Cross-user scoping
A strategy (and its risks, roadmap, portfolio, fit, evidence-health, PDF export)
belonging to another profile is indistinguishable from a missing one:
**404 `NOT_FOUND`, never 403**.

## Failure simulation
POST `/strategies/{strategy_id}/simulate`

Example:
```json
{
  "scenario": "TOP_3_REJECTED"
}
```

Other scenarios:
- `BUDGET_MINUS_25_PERCENT`
- `IELTS_LOWERED`
- `REMOVE_COUNTRY`
- `DEADLINE_MISSED`
- `CUSTOM`

Optional second key, `modifications` (passed through as the scenario's extra
overrides):
- `REMOVE_COUNTRY` accepts `{"modifications": {"country": "Germany"}}` — the
  named country is dropped (and its programs leave the regenerated pool) even
  when it is not the first preferred country. Without it the first preferred
  country is removed. Country names resolve through the countries table; an
  unresolvable name disables the filter rather than dropping programs on a guess.
- `CUSTOM` merges `modifications` verbatim into the profile snapshot.

Response:
```json
{
  "counterfactual_run_id": "uuid",
  "scenario": "REMOVE_COUNTRY",
  "modified_profile": {},
  "portfolio_before": [],
  "portfolio_after": [],
  "delta": {"summary": "..."}
}
```
`portfolio_before`/`portfolio_after` are arrays of portfolio rows (same row shape
as `GET /strategies/{id}/portfolio`). The re-tier is a pure computation over the
strategy's already-known candidates: no plan rows are written and no research run
starts. Unknown `scenario` → `422 VALIDATION_ERROR`.

## Monitoring
POST `/monitor/subscriptions`
GET `/monitor/subscriptions`
POST `/monitor/subscriptions/{id}/check`
GET `/monitor/subscriptions/{id}/changes`

`POST /monitor/subscriptions` body:
```json
{ "field_key": "deadline", "frequency": "WEEKLY", "program_id": "uuid" }
```
`field_key` is required and must be one of `deadline | cost | academic |
prerequisite | scholarship`; `frequency` defaults to `WEEKLY` and accepts
`DAILY | WEEKLY | MONTHLY`; `program_id` is optional. Returns `{"id": "uuid"}`.
`POST .../check` → `{id, change_type, material_change, old_value, new_value,
explanation}` for the snapshot it just took.
`GET .../changes` → `{"items": [{id, change_type, material_change, checked_at,
old_value, new_value, explanation}]}`.

Subscriptions are profile-scoped: another profile's subscription id (or an
unknown one) is `404 NOT_FOUND`, never an empty `200`.

## Evidence
GET `/evidence` — optional `?program_id=uuid`, newest first, capped at 50 rows
GET `/evidence/{evidence_id}`
GET `/evidence/{evidence_id}/conflicts`
POST `/evidence/{evidence_id}/recheck`
POST `/evidence/conflicts/{conflict_id}/resolve`

Evidence and programs are shared catalog content: ids are readable by any caller
(no owner scoping), an unknown id is `404 NOT_FOUND`.

`GET /evidence/{evidence_id}/conflicts` →
`{"evidence_id": "uuid", "conflicts": [{id, conflict_key, description,
resolution_status, preferred_evidence_id, resolution_reason, resolved_at}]}`.

`POST /evidence/{evidence_id}/recheck` — bounded fresh re-check (one search +
extraction). Returns the evidence object plus:
```json
{ "recheck": {"query": "...", "refreshed": false, "new_evidence_id": null, "found": false} }
```
`refreshed: true` means the stored claim was re-verified (freshness refreshed) or
a differing value was stored as a **new** evidence row (`new_evidence_id`) with
conflict detection run; the original row is never overwritten. Errors:
- `404 NOT_FOUND` — unknown evidence id.
- `409 PROVIDER_UNAVAILABLE` — `SERPAPI_API_KEY` is not configured; no fake data
  is ever substituted.
- `502` (provider code) — the upstream search itself failed.

`POST /evidence/conflicts/{conflict_id}/resolve` — body (both keys optional, an
empty body is valid):
```json
{ "preferred_evidence_id": "uuid", "reason": "official source wins" }
```
Defaults to the authority-preferred member recorded at detection time; only
members of this conflict may be chosen. Response:
```json
{
  "id": "uuid",
  "conflict_key": "...",
  "resolution_status": "RESOLVED",
  "preferred_evidence_id": "uuid",
  "resolution_reason": "...",
  "resolved_at": "ISO-8601"
}
```
Errors: `404 NOT_FOUND` (unknown conflict or evidence id), `409 ALREADY_RESOLVED`,
`409 EVIDENCE_NOT_IN_CONFLICT` (chosen evidence is not a member),
`409 EMPTY_CONFLICT` (conflict has no members).

## Documents
GET `/documents`
POST `/documents`
PATCH `/documents/{document_id}`

`GET` lazily provisions the eight standard readiness checklist items
(`transcript, passport, language_score, cv, sop, lors, portfolio,
financial_proof`) and returns `{items: [{id, document_type, status, expires_at}]}`.
`POST` body `{document_type, notes?}` → `{id, document_type, status}`;
`PATCH` body `{status?, notes?}` → `{id, status}` where `status` is one of
`TODO | IN_PROGRESS | DONE | BLOCKED | SKIPPED`. Documents are profile-scoped:
another profile's document id is `404 NOT_FOUND`, never 403.

## Notifications
GET `/notifications` → `{"items": [...], "unread_count": <int>}` (newest first, capped at 50)
POST `/notifications/{notification_id}/read` → `{"read": true}`
POST `/notifications/read-all` → `{"marked": <int>}`

Item keys: `id`, `type`, `title`, `body`, `link`, `read` (bool),
`email_status`, `created_at`. Notifications are per user: a missing or foreign
notification id is `404 NOT_FOUND`.

## Export
POST `/strategies/{strategy_id}/export/pdf`

## Admin/observability
GET `/admin/search-usage`
GET `/admin/research-runs`

Both require role `ADMIN`: authenticated non-admins get `403 FORBIDDEN`
(anonymous callers are judged by the local demo user's stored role).

Do not expose SerpApi keys or raw provider credentials.

## API conventions
- UUID identifiers.
- ISO-8601 timestamps.
- Pagination using `page`, `page_size`, `next_cursor` where appropriate.
- `page_size` max 100.
- All expensive research endpoints return asynchronous job IDs.
- Use idempotency keys for research creation: `POST /research/plan` and
  `POST /research/runs` accept an `Idempotency-Key` header (same semantics).
- Every response gets `X-Request-ID` (plus `X-Response-Time-Ms`).
- Never return provider secrets.
- **Cross-user scoping**: strategies, research runs, monitor subscriptions,
  documents and notifications that belong to another profile return
  `404 NOT_FOUND` (never 403) — a foreign id is indistinguishable from a
  non-existent one. Programs and evidence are shared catalog rows and are not
  owner-scoped.
- **Rate limits** (per client IP, 60-second window, default
  `rate_limit_per_minute` = 60/min, `429 RATE_LIMITED` envelope): counted for
  `POST /research/runs`, `POST /research/plan`, `POST /research/demo`,
  `POST /monitor/subscriptions`, `POST /auth/login`, `POST /auth/register` and
  every `POST /evidence/{evidence_id}/recheck`. All other paths and all
  non-POST methods are excluded from the counter.
- **Body size limit**: `POST`/`PATCH`/`PUT` bodies over 1,000,000 bytes are
  rejected with `413 PAYLOAD_TOO_LARGE` before reaching the route.
- CORS allows the headers `Content-Type`, `Authorization`, `Idempotency-Key`
  and `X-Request-ID`.
