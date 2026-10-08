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
npm run lint        # ESLint (next/core-web-vitals + next/typescript)
npx tsc --noEmit    # type check
```

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
│   ├── loading.tsx (per route)  # route-level skeletons
│   ├── login/  register/        # auth (React Hook Form)
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
│   │   ├── evidence-actions.tsx # Re-check / Resolve conflict
│   │   └── risk-actions.tsx     # Acknowledge / Resolve / Dismiss
│   └── lib/
│       ├── api.ts               # typed API client + error handling
│       └── schemas.ts           # zod (loose/passthrough) response schemas
├── tailwind.config.ts
├── next.config.mjs
└── package.json
```

## Pages

| Route | What it does |
| --- | --- |
| `/` | Hero: "Study abroad with a plan, not a pile of tabs." |
| `/login`, `/register` | Sign in / create account. A 401 mid-session redirects to `/login?expired=1&next=…` and returns you to the page you were on. |
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
- **Loading** — each data-heavy route has a `loading.tsx` skeleton.
- **Copy** — student-facing severity and status wording comes from
  `components/ui.tsx` (`SEVERITY_COPY`, `STATUS_COPY`), matching the spec.

## API contract

All endpoints are documented in [`../api/API_CONTRACT.md`](../api/API_CONTRACT.md)
(read-only reference). The client lives in `app/lib/api.ts`.
