# AdmitGraph Test Plan

## Unit tests
1. CGPA normalization.
2. Percentage normalization.
3. Language score threshold.
4. Test expiry.
5. Prerequisite subject matching.
6. Credit matching.
7. Deadline comparison.
8. Budget comparison.
9. Evidence freshness.
10. Source authority.
11. Evidence conflict.
12. Fit score calculation.
13. Risk severity.
14. Portfolio category.
15. Portfolio diversification.
16. Counterfactual scenario transformations.

## Required test cases
### Complete profile
Expected: strategy generated with evidence.

### Missing IELTS
Expected: language risk; no invented score.

### Insufficient prerequisite
Expected: critical/high risk and verification action.

### Conflicting deadline
Expected: conflict visible; confidence downgraded.

### No official source
Expected: UNKNOWN.

### Low budget
Expected: financial risk and alternatives.

### Deadline passed
Expected: program excluded or marked unavailable for selected intake.

### All top programs risky
Expected: broaden strategy.

### SerpApi timeout
Expected: partial/cached evidence if valid; no UI crash.

### LLM malformed JSON
Expected: validation retry then safe failure.

### Provider rate/credit error
Expected: friendly error and cached evidence where valid.

### Duplicate program
Expected: canonicalization prevents duplicate program records.

### Stale evidence
Expected: visible stale state and recheck option.

### Monitoring change
Expected: snapshot and material-change alert.

## Security tests
- API key not present in frontend build.
- CORS blocks unknown origin.
- rate limit expensive endpoints.
- malformed UUID rejected.
- oversized payload rejected.
- arbitrary URL fetching blocked.
- logs contain no secret.
- authorization prevents cross-user profile access.

## Contract tests
OpenAPI schemas match frontend client types.
