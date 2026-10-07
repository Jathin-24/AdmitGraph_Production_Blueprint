# Backend Implementation Specification

## Structure
backend/
  app/
    main.py
    api/
      v1/
        profile.py
        onboarding.py
        research.py
        programs.py
        strategies.py
        evidence.py
        monitor.py
        documents.py
    core/
      config.py
      security.py
      logging.py
      errors.py
    db/
      session.py
      models/
      repositories/
      migrations/
    schemas/
    services/
      serpapi/
      research/
      evidence/
      matching/
      scoring/
      risk/
      strategy/
      monitoring/
    workers/
    tests/

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
- Argon2/bcrypt only if local auth is implemented.
- Prefer managed OAuth if auth is needed.
- CORS allowlist.
- Rate limiting.
- Request validation.
- Maximum body size.
- SSRF protection for any server-side URL fetching.
- Never accept arbitrary URLs for backend fetching without allowlisting and validation.
- Sanitize HTML if rendered.
- Do not store unnecessary personal data.
- Encrypt secrets using platform secret manager.
- Do not log profile PII, tokens, or API keys.

## Reliability
- timeouts on all provider calls
- retry only transient failures
- circuit breaker around provider
- cache normalized search results
- graceful partial strategy
- explicit `PARTIAL` run status
- structured logs with request_id and run_id

## Tests
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
