# SerpApi Integration — Verified Implementation Notes

Official documentation references:
- Google Search: https://serpapi.com/search-api
- Google Jobs: https://serpapi.com/google-jobs-api
- Google News: https://serpapi.com/google-news-api
- Google Scholar: https://serpapi.com/google-scholar-api
- Google Trends: https://serpapi.com/google-trends-api
- Google Maps: https://serpapi.com/google-maps-api

## Current verified API shape
SerpApi uses the search endpoint:
`https://serpapi.com/search?engine=<engine>`

Google Search requires `q`. Location, `gl`, `hl`, pagination and other engine-specific parameters are optional depending on the use case.

Google Jobs uses:
`engine=google_jobs`
and requires `q`.

Google News uses:
`engine=google_news`.
The API supports query/localization parameters and returns structured `news_results`.

Google Scholar uses:
`engine=google_scholar`
and requires `q` unless using supported alternate lookup parameters.

Google Trends uses:
`engine=google_trends`; `q` is required. Current documentation supports up to five queries for TIMESERIES/GEO_MAP comparison scenarios.

Google Maps uses:
`engine=google_maps`; search uses `q` and geographic parameters as appropriate.

## Integration rules
1. Store API key only in backend environment variables.
2. Never expose it in Next.js client bundles.
3. Wrap SerpApi behind `SerpApiClient`.
4. Do not let product code construct raw provider URLs.
5. Every request is recorded in `search_runs`.
6. Persist provider search ID when available.
7. Persist query + non-secret parameters.
8. Persist response metadata and normalized results.
9. Cache identical searches.
10. Respect provider caching behavior and avoid forcing `no_cache=true` unless a fresh check is explicitly required.
11. Bound query count per strategy run.
12. Run independent searches concurrently with a safe concurrency limit.
13. Retry transient failures with exponential backoff and jitter.
14. Never retry permanent validation/authentication failures indefinitely.
15. If provider fails, use recent evidence if still within its freshness window and label the result as cached.
16. If no evidence exists, return UNKNOWN rather than fabricate data.

## Engine routing
P0:
- google

P1:
- google_jobs
- google_news

P2:
- google_scholar
- google_trends
- google_maps

## Search query templates
Use generated queries, not hard-coded Germany-only queries.

Requirements:
`site:{official_domain} {program} admission requirements {intake_year}`
`site:{official_domain} {program} IELTS TOEFL language requirements`
`site:{official_domain} {program} prerequisites credits modules`
`site:{official_domain} {program} application deadline {intake_year}`
`site:{official_domain} {program} tuition fees`

Country policy:
`{country} international student visa financial proof official`
`site:{official_government_domain} student visa financial proof`

Scholarships:
`site:{official_domain} international students scholarship {program/country}`

Career:
`{field} jobs {city} {country}`
Use Google Jobs for career signal rather than treating general search results as job-market data.

News:
`{country} student visa policy international students`
`{university} tuition international students`
`{university} admissions policy {program}`

## Result classification
Classify domains using:
1. official university
2. official government
3. accredited/recognized body
4. credible secondary
5. news
6. forum/social
7. unknown

The classifier must be deterministic where possible using domain allowlists/heuristics and can use an LLM only as a secondary classifier.

## Extraction contract
LLM receives:
- normalized search result
- source metadata
- exact snippet/content available to the system
- extraction schema

LLM returns:
```json
{
  "claims": [
    {
      "claim_type": "LANGUAGE_REQUIREMENT",
      "normalized_key": "english_ielts_overall",
      "value": {"test":"IELTS","overall":6.5},
      "mandatory": true,
      "claim": "The program requires IELTS overall 6.5.",
      "evidence_id": "uuid"
    }
  ]
}
```

No evidence ID = reject the extracted claim.

## Conflict handling
Two claims for the same normalized key are conflicting if their normalized values differ and both are temporally relevant.

Do not overwrite old evidence.
Create a conflict record and keep both claims.

## Freshness
Store freshness policy in application configuration so it can change without migrations.

## Important provider behavior
SerpApi documentation states cached searches can be returned when query and parameters match; current provider docs also document `no_cache`, `async`, `output`, and JSON restriction behavior for several engines. The implementation should use JSON for normal processing and only use markdown output where it provides a clear LLM-processing benefit.

## API usage budget
Implement a per-run budget:
- discovery: max 6 searches
- requirements per shortlisted program: max 4
- funding: max 2
- career: max 2
- news: max 2
- optional engines only after core success

These are application-level safeguards, not SerpApi account limits.
