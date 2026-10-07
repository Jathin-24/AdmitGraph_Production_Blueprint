# AdmitGraph — Master Product & Engineering Specification

## 1. Product definition
AdmitGraph turns a student's profile and goals into a continuously updated, evidence-backed study-abroad strategy by discovering programs, checking requirements, calculating practical fit and risk, identifying missing actions, and monitoring changes.

The product is not:
- a generic university finder
- a generic chatbot
- an SOP generator
- a university marketplace
- an admission-probability predictor
- a static database claiming every institution/program

## 2. Primary user
A first-time international applicant who may not understand admissions terminology, country differences, prerequisites, language tests, finances, deadlines, or visa planning.

The UX must teach while collecting data. Every technical term gets a plain-language explanation.

## 3. Core user journey
1. Welcome / “What does studying abroad involve?”
2. Goal discovery.
3. Education profile.
4. Academic performance.
5. English/language tests.
6. Experience, projects, skills.
7. Budget and funding.
8. Countries/cities.
9. Intake and timeline.
10. Career goal.
11. Generate Strategy.
12. Live SerpApi research progress.
13. Strategy dashboard.
14. Program detail.
15. Eligibility matrix.
16. Risks and blockers.
17. Evidence drawer.
18. Application portfolio.
19. Action roadmap.
20. Failure simulator.
21. Change monitor.

## 4. Onboarding questions
The system should ask progressively, not as one giant form.

### Goal
- Why do you want to study abroad?
- What degree do you want?
- What field/specialization?
- When do you want to start?
- Which countries are you considering?
- Are you open to countries you have not considered?

### Academic
- Current degree
- Institution
- Institution country
- Field
- Graduation year
- CGPA/percentage
- grading scale
- backlogs
- relevant subjects
- relevant credits if known

### Tests
- IELTS/TOEFL/PTE/other
- overall score
- section scores
- test date
- expiry date
- GRE/GMAT if relevant

### Experience
- internships
- work
- projects
- research
- publications
- skills

### Financial
- total available budget
- annual budget if known
- tuition-only ceiling
- living-cost tolerance
- scholarship dependence
- willingness to work part-time
- funding source

### Preferences
- preferred country
- city size
- public/private
- research/coursework preference
- language preference
- job-market importance
- climate/culture preferences
- distance from home preference

### Constraints
- must-have countries
- must-not countries
- family constraints
- application deadline constraints
- language limitations
- financial ceiling

## 5. Education-first UX
Before asking for complex data, explain:
- what a master's program is
- what “intake” means
- what a prerequisite is
- what ECTS/credits may mean
- what language requirements mean
- what a deadline means
- what tuition vs living cost means
- what scholarships can and cannot cover
- what a visa/financial-proof requirement is

Use “Why are we asking this?” helper text.

## 6. Decision model
For each program, calculate:
- academic fit
- prerequisite fit
- language/test fit
- financial fit
- career fit
- timing fit
- evidence confidence

Initial weights:
- academic 25
- prerequisites 20
- language/test 10
- financial 15
- career 15
- timing 10
- evidence confidence 5

The score must be recalculable from stored inputs and scoring-rule version.

## 7. Eligibility states
Each requirement dimension must use:
- SATISFIED
- PARTIAL
- NOT_SATISFIED
- UNKNOWN
- NOT_APPLICABLE
- CONFLICTING
- NEEDS_VERIFICATION

A requirement must include its evidence references.

## 8. Risk model
Risk types:
- ELIGIBILITY
- PREREQUISITE
- LANGUAGE
- TEST
- DEADLINE
- FINANCIAL
- DOCUMENT
- VISA_COMPLIANCE
- CAREER
- INFORMATION_FRESHNESS
- SOURCE_CONFLICT
- DATA_QUALITY

Severity:
- CRITICAL
- HIGH
- MEDIUM
- LOW

Each risk has:
- reason
- evidence IDs
- affected requirement
- recommended action
- status
- detected_at
- resolved_at
- confidence

## 9. Strategy portfolio
Default:
- 1–2 Reach
- 3–4 Target
- 1–2 Safety / Lower-risk

Do not use “safety” to imply guaranteed admission. UI label should be “Lower-risk fit”.

Portfolio generation must maximize robustness, not application count.

## 10. Evidence graph
Important claims are graph nodes:
- program
- requirement
- deadline
- fee
- scholarship
- policy
- career signal

Evidence records point to:
- source URL
- source domain
- source authority
- search engine
- query
- retrieved_at
- publication date if known
- snippet/summary
- content hash
- freshness window
- confidence
- conflict group

A recommendation points to the exact evidence supporting it.

## 11. Conflict policy
Never silently choose between conflicting sources.
If official sources conflict:
- show conflict
- rank authority
- show timestamps
- ask the user to verify
- downgrade confidence
- create a SOURCE_CONFLICT risk

## 12. Freshness
Each claim type has a configurable freshness window:
- deadlines: 7 days
- tuition/fees: 30 days
- language requirements: 30 days
- prerequisites: 30 days
- visa/financial rules: 7 days
- scholarships: 7 days
- career signals: 14 days
These are defaults, not legal guarantees.

## 13. SerpApi usage
P0:
- Google Search

P1:
- Google Jobs
- Google News

P2:
- Google Scholar
- Google Trends
- Google Maps/Local

Search results must be stored before reasoning.

## 14. Agent architecture
Use deterministic orchestration rather than autonomous agents making uncontrolled calls.

Orchestrator:
1. validates profile
2. creates research plan
3. executes bounded searches
4. normalizes results
5. extracts claims
6. resolves duplicates/conflicts
7. evaluates requirements
8. calculates scores
9. generates risks
10. generates strategy
11. persists a complete strategy run

Agents/services:
- DiscoveryService
- RequirementEvidenceService
- FundingService
- CareerSignalService
- PolicySignalService
- MatchingService
- RiskService
- StrategyService
- MonitoringService

Every service has typed input/output schemas.

## 15. Anti-hallucination policy
- LLM receives evidence, never raw unsupported assumptions.
- Extraction must return evidence IDs.
- Missing evidence = UNKNOWN.
- Structured JSON validation is mandatory.
- Invalid LLM output is retried with a repair prompt, then fails safely.
- No admission probability.
- No definitive legal/visa advice.
- Official government/university evidence is preferred.
- Secondary sources can corroborate but cannot silently override official evidence.

## 16. Hackathon differentiators
1. Risk-first recommendations.
2. Evidence graph.
3. Failure simulator.
4. Freshness/conflict awareness.
5. “Don't apply here” decision.
6. Plan health score.
7. What changed since your last check.
8. Counterfactual portfolio regeneration.
9. Explainable scoring.
10. Live SerpApi activity visible during demo.

## 17. Product-grade extras
### Plan Health
A single dashboard health indicator derived from:
- blocker count
- unresolved conflicts
- stale evidence
- deadline proximity
- financial feasibility
- document readiness

### What Changed?
Compare current evidence with prior snapshots and explain only material changes.

### Counterfactual Simulator
Examples:
- IELTS 6.5 instead of 7.5
- budget reduced by ₹4 lakh
- top 3 rejected
- one country removed
- graduation delayed

Regenerate portfolio and show the delta.

### Application Readiness
Track documents without becoming a full application marketplace:
- transcript
- passport
- language score
- CV
- SOP status
- recommendation letters
- portfolio
- financial proof

### Evidence Health
Show:
- verified claims
- stale claims
- conflicts
- unknowns
- source authority distribution

## 18. Demo scenario
Indian B.Tech CSE student:
- CGPA 8.1
- IELTS 7.5
- one internship
- ₹18 lakh target budget
- MSc AI
- Germany
- 2027 intake

The system should surface a personalized insight such as:
“Your biggest risk is not IELTS. It is prerequisite/credit equivalency.”

This is an example insight, not a hard-coded output.

## 19. Non-functional requirements
- API p95 target under 500 ms for cached CRUD operations.
- Research jobs are asynchronous.
- UI must show progress, partial results, and failures.
- Idempotent research runs.
- Request IDs in every backend response.
- Structured logs.
- Secrets never sent to browser.
- Rate limiting on expensive endpoints.
- Database migrations mandatory.
- Unit + integration + contract tests mandatory.
- Production error states must be designed, not generic.

## 20. MVP cut line
Must:
profile, discovery, requirements, eligibility, scoring, risk, portfolio, evidence, roadmap, live SerpApi.

Should:
jobs, news, failure simulator, change recheck, PDF export.

Could:
scholar, trends, maps, notifications, calendar.

Do not build before demo:
full application submission, payments, loans, human counselling marketplace, native app, automated visa filing.
