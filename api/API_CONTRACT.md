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

## Health
GET `/health`
GET `/health/ready`

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

## Research
POST `/research/plan`
POST `/research/runs`
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
GET `/strategies/{strategy_id}/roadmap`
GET `/strategies/{strategy_id}/evidence-health`

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

## Monitoring
POST `/monitor/subscriptions`
GET `/monitor/subscriptions`
POST `/monitor/subscriptions/{id}/check`
GET `/monitor/subscriptions/{id}/changes`

## Evidence
GET `/evidence/{id}`
GET `/evidence/{id}/conflicts`

## Documents
GET `/documents`
POST `/documents`
PATCH `/documents/{id}`

## Export
POST `/strategies/{id}/export/pdf`

## Admin/observability
GET `/admin/search-usage`
GET `/admin/research-runs`

Do not expose SerpApi keys or raw provider credentials.

## API conventions
- UUID identifiers.
- ISO-8601 timestamps.
- Pagination using `page`, `page_size`, `next_cursor` where appropriate.
- `page_size` max 100.
- All expensive research endpoints return asynchronous job IDs.
- Use idempotency keys for research creation.
- Every response gets `X-Request-ID`.
- Never return provider secrets.
