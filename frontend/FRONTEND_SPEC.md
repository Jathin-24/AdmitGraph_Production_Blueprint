# Frontend UX & Implementation Specification

## Design principle
The user should never need to understand study-abroad jargon before using the product.

Visual direction:
- calm, premium, trustworthy
- data-rich without looking like enterprise software
- progressive disclosure
- strong hierarchy
- accessible contrast
- responsive desktop/mobile
- minimal decorative animation
- evidence and risk states visually obvious

## Main navigation
- Home / Strategy
- Explore
- My Plan
- Monitor
- Profile

## First screen
Hero:
“Study abroad with a plan, not a pile of tabs.”

Subtext:
“AdmitGraph checks live requirements, costs, deadlines and risks against your profile — then tells you what to do next.”

Primary CTA:
“Build my strategy”

Secondary:
“First time studying abroad? Start here”

## Beginner mode
Provide a 3-step educational intro:
1. Choose what you want to study.
2. Tell us about your profile and budget.
3. We research current evidence and build your plan.

Never open with a 50-field form.

## Profile wizard
Use one decision per screen where possible.
Progress indicator:
Goal → Education → Tests → Experience → Budget → Preferences → Review

Each question:
- title
- one-sentence explanation
- input
- example
- “Why we ask this”
- skip/unknown when valid

Autosave every step.

## Research screen
Show real-time stages:
✓ Understanding your profile
✓ Finding candidate programs
● Verifying requirements
○ Checking costs and deadlines
○ Building risk profile
○ Building strategy

Display:
“Live research powered by SerpApi”
and count of sources checked.

Do not fake progress. Only show completed steps when backend events confirm them.

## Strategy dashboard
Top:
- Strategy health
- one-sentence insight
- urgent action

Then:
- Reach
- Target
- Lower-risk fit

Each card:
- fit score
- top 2 reasons
- top risk
- next deadline
- estimated cost band
- evidence freshness
- CTA “Why this recommendation?”

## Program detail
Sections:
1. Overview
2. Why it fits
3. Eligibility matrix
4. Risks
5. Cost
6. Deadline
7. Career signal
8. Evidence
9. Next actions

## Eligibility matrix
Rows:
Academic background
Prerequisites
Language
Tests
Documents
Deadline
Financial
Country/policy

Columns:
You / Requirement / Status / Evidence

Status copy must be beginner-friendly:
“Looks good”
“Needs verification”
“Likely blocker”
“Not required”
“Not enough evidence yet”

## Evidence drawer
For every claim:
- Claim
- Source
- Authority
- Retrieved
- Freshness
- Confidence
- Source conflict status
- Open source

Avoid dumping raw search results.

## Risk UI
Critical:
“Do not apply yet”
High:
“Fix before applying”
Medium:
“Verify before deciding”
Low:
“Good to know”

## Failure simulator
Friendly framing:
“What could break this plan?”

Scenario cards:
- My top 3 reject me
- My budget drops
- My IELTS score is lower
- I remove Germany
- I miss the next deadline

Show before/after portfolio and explain the change.

## Monitoring
“Your plan is alive.”
Cards:
- Requirement changed
- Deadline changed
- New scholarship signal
- Source became stale
- Conflicting information detected

## Accessibility
- keyboard navigable
- semantic HTML
- ARIA only where needed
- visible focus
- no color-only meaning
- reduced-motion support
- screen-reader labels

## Frontend architecture
Use:
- server components where appropriate
- client components only for interactive pieces
- TanStack Query for server state
- Zod for client validation
- typed API client generated from OpenAPI if practical
- React Hook Form for wizard forms

Do not duplicate backend business logic in frontend.

## Error states
Design explicit states:
- no results
- partial research
- provider unavailable
- stale evidence
- conflicting evidence
- missing profile data
- session expired
- export failed

Never show “Something went wrong” alone.

## Performance
- stream/poll research progress
- skeletons
- route-level loading
- cache strategy results
- avoid rendering giant evidence payloads
- virtualize long lists if necessary
