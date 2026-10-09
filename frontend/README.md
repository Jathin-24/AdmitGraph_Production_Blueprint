# AdmitGraph — Frontend

The student-facing Next.js (App Router) app for AdmitGraph. Every screen is
driven by real backend responses — nothing on the UI is faked or stubbed.

Spec: [`FRONTEND_SPEC.md`](./FRONTEND_SPEC.md) (copy and behaviour in this
README follow it).

## Getting started

```bash
npm install
npm run dev     # http://localhost:3000
```

Other scripts:

```bash
npm run lint             # ESLint (next/core-web-vitals + next/typescript)
npm run typecheck        # tsc --noEmit
npx tsc --noEmit         # same, without the script alias
npm test                 # Vitest unit/component tests (jsdom)
npm run test:watch       # watch mode
npm run build            # production build (stop `npm run dev` first)
npm run format           # Prettier write
npm run format:check     # Prettier check (no writes)
npm run gen:api-types    # regenerate app/lib/api-types.generated.ts from
                         # ../api/openapi.json (committed; CI diffs it)
```

CI order is lint → gen:api-types freshness → test → build → typecheck: `next
build` generates the gitignored `next-env.d.ts`, so the type check must run
after a build (or a dev server) on a fresh checkout.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000/api/v1` | Base URL of the backend API. Set this whenever the API is not on `localhost:8000` (e.g. a deployed backend). |

Create `frontend/.env.local` for local overrides:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

The backend must be running for data to appear; the app shows explicit error
states (never a bare "Something went wrong") when it is not.

## Structure

```
frontend/
├── app/
│   ├── page.tsx                 # landing / hero
│   ├── layout.tsx               # shell + providers
│   ├── globals.css              # design tokens, chip/card/btn classes
│   ├── loading.tsx              # root skeleton (per-route copies on admin,
│   │                            #   dashboard, explore, monitor,
│   │                            #   notifications, programs/[id], research)
│   ├── error.tsx                # app-wide error boundary (root only)
│   ├── not-found.tsx            # 404 screen (root only)
│   ├── login/  register/        # auth (React Hook Form)
│   ├── forgot-password/         # POST /auth/forgot
│   ├── reset-password/          # consumes the emailed ?token= link
│   ├── verify-email/            # consumes the emailed verification ?token= link
│   ├── admin/                   # ADMIN-only usage + research-run console
│   ├── onboarding/              # profile wizard (autosaves every step)
│   ├── profile/                 # profile editor
│   ├── research/                # live research run + stages
│   ├── dashboard/               # "My Plan": portfolio, risks, simulator
│   ├── explore/                 # program discovery
│   ├── programs/[id]/           # program detail (9 sections)
│   ├── monitor/                 # subscriptions, checks, changes
│   ├── notifications/           # notification feed
│   ├── components/
│   │   ├── ui.tsx               # shared primitives (chips, skeleton, fmt…)
│   │   ├── nav.tsx              # responsive nav + mobile disclosure
│   │   ├── account.tsx          # session menu / sign-out
│   │   ├── notifications-bell.tsx
│   │   ├── api-health-banner.tsx# backend-down warning
│   │   ├── evidence-actions.tsx # Re-check / Resolve conflict
│   │   └── risk-actions.tsx     # Acknowledge / Resolve / Dismiss
│   └── lib/
│       ├── api.ts               # typed API client + error handling (only
│       │                        #   module besides api-extra.ts allowed to fetch)
│       ├── api-extra.ts         # forgot/reset/verify + secondary endpoints
│       ├── api-types.generated.ts # from `npm run gen:api-types` — do not edit
│       ├── schemas.ts           # zod (loose/passthrough) response schemas
│       └── auth.tsx             # session context
├── vitest.config.ts             # jsdom + @ alias + setup
├── vitest.setup.ts
├── *.test.ts(x)                 # 7 test files, 30 tests (see Tests below)
├── tailwind.config.ts
├── next.config.mjs
└── package.json
```

The `/verify-email` page (`app/verify-email/page.tsx`) consumes the emailed
`?token=` link: it calls `POST /auth/verify` automatically on arrival and
handles each outcome — verifying, verified, missing token, invalid/expired
link, and server/route errors — with a "Resend verification email" button for
signed-in accounts (`POST /auth/verify-request`).

## Pages

| Route | What it does |
| --- | --- |
| `/` | Hero: "Study abroad with a plan, not a pile of tabs." |
| `/login`, `/register` | Sign in / create account. A 401 mid-session redirects to `/login?expired=1&next=…` and returns you to the page you were on. |
| `/forgot-password`, `/reset-password` | Password recovery: request a link, then consume the emailed `?token=`. Both endpoints answer the same 202 whether or not the account exists. |
| `/verify-email` | Consumes the emailed verification `?token=`: auto-verifies on arrival and handles verified / invalid / missing / error states, with a resend button for signed-in accounts. |
| `/admin` | ADMIN-only console: SerpApi search usage and research runs. Backend returns 403 for anonymous and non-admin callers alike (the demo account is STUDENT); the page renders an "admins only" notice instead of data. |
| `/onboarding` | Guided profile wizard (Goal → Education → Tests → Experience → Budget → Preferences → Review), one decision per screen, autosave every step. |
| `/profile` | Edit the saved profile. |
| `/research` | Start a research run and watch real backend events: six student-facing stages plus a step-by-step detail view. Live runs are powered by SerpApi server-side. |
| `/dashboard` | "My Plan" — plan health, urgent action, portfolio cards (fit score, top reasons, top risk, next deadline, cost band, evidence freshness), evidence drawer, risks with actions, roadmap, and the failure simulator. |
| `/explore` | Search and filter programs, save/unsave. |
| `/programs/[id]` | Program detail: overview, why it fits, eligibility matrix, risks, cost, deadline, career signal, evidence, next actions. |
| `/monitor` | Subscriptions, checks and detected changes. |
| `/notifications` | Notification feed. |

## Architecture notes

- **Server state** — TanStack Query everywhere; query keys are shared across
  pages so caches stay consistent.
- **Validation** — zod schemas in `lib/schemas.ts` are deliberately loose
  (`looseObject`/passthrough): unknown or newly added backend fields never break
  the UI, and a schema mismatch falls back to the raw payload instead of
  throwing.
- **Forms** — React Hook Form for login, register, profile and the onboarding
  wizard. Client-side rules mirror **only** what the backend enforces; the
  frontend never invents constraints.
- **Errors** — `ApiError` carries HTTP status + backend error `code`, so the UI
  can react precisely (409 provider unavailable, 404 gone, 502 upstream, …) and
  always says what happened and what to do next.
- **Session expiry** — a 401 with a stored token clears the token, shows
  "session expired, please sign in again", and redirects once to `/login` with
  the original route preserved.
- **Loading** — `loading.tsx` skeletons on the eight data-heavy routes (root,
  admin, dashboard, explore, monitor, notifications, programs/[id], research);
  `error.tsx` / `not-found.tsx` live at the app root and cover every route.
- **Copy** — student-facing severity and status wording comes from
  `components/ui.tsx` (`SEVERITY_COPY`, `STATUS_COPY`), matching the spec.
- **Rate-limited / rejected calls** — `ApiError.status` drives precise copy
  (`409 PROVIDER_UNAVAILABLE` for rechecks without a SerpApi key,
  `429 RATE_LIMITED`, `429 RESOURCE_EXHAUSTED` at the concurrent-run cap).

## Tests

```bash
npm test          # Vitest + jsdom + Testing Library — 7 files, 30 tests
```

| File | Covers |
| --- | --- |
| `app/lib/api.test.ts` | `apiFetch` URL building, auth header, error envelope, retry |
| `app/lib/schemas.test.ts` | zod schemas accept real payloads, reject malformed ones |
| `app/lib/api-contract.test.ts` | `../api/openapi.json` ↔ generated types ↔ endpoints the client calls |
| `app/providers-retry.test.tsx` | React Query retry policy (no retry on 4xx) |
| `app/components/nav.test.tsx` | nav rendering + accessible mobile disclosure |
| `app/explore/filters-url.test.tsx` | explore filters ↔ URL sync |
| `app/source-scan.test.ts` | no bare `fetch(` outside `lib/api.ts`/`api-extra.ts`; no secret-shaped literals |

There is no browser/E2E suite yet (see `../tests/TEST_PLAN.md` for the full
plan and the documented gaps).

## API contract

All endpoints are documented in [`../api/API_CONTRACT.md`](../api/API_CONTRACT.md)
(read-only reference), with the machine-readable spec in
[`../api/openapi.json`](../api/openapi.json). The client lives in
`app/lib/api.ts` + `app/lib/api-extra.ts`; `app/lib/api-types.generated.ts` is
generated from the spec — edit neither by hand.
