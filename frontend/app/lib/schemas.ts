/**
 * Zod schemas for the API reads we depend on (FRONTEND_SPEC §Frontend
 * architecture: "Zod for client validation").
 *
 * PARSE RULE (choose per call site — never let a page white-screen):
 *
 * 1. STRICT collections — every schema whose payload a page calls `.map()`
 *    over (strategies, portfolio, risks, evidence, notifications, programs,
 *    subscriptions, fit, research steps, the onboarding wizard steps) is
 *    wrapped in `strictParser()`. A mismatch throws a typed `ParseError`
 *    from inside the react-query `queryFn`, so the query lands in its error
 *    path and the page renders its error state + retry — never a raw payload
 *    that explodes on `.map()`.
 * 2. LENIENT metadata — everything else (auth responses, run status echoes,
 *    counters that only feed a label) stays loose: unknown extra fields pass
 *    through, a mismatch falls back to the raw payload (and warns in dev).
 *    Inside strict schemas, display-only/metadata fields (unread_count,
 *    totals, nullable notes) use `.catch()` defaults so one drifted counter
 *    never blanks a working list.
 *
 * Item schemas are deliberately LOOSE objects: a newer backend can add
 * fields without breaking the UI; only the fields the UI depends on are
 * required.
 */

import { z } from "zod";

/** Structural shape of a zod parser — keeps api.ts free of zod generics.
 *  The flag is named `strictParse` (not `strict`) on purpose: zod's own
 *  schemas expose a `.strict()` method, and colliding with it would make
 *  every direct schema→apiFetch assignment fail to type-check. */
export interface LooseParser<T> {
  /** Strict parsers throw `ParseError` on mismatch (see rule 1 above). */
  strictParse?: boolean;
  safeParse(data: unknown): { success: true; data: T; error?: unknown } | { success: false; error?: unknown };
}

/** A response that does not match its schema. Typed so react-query callers
 *  (and providers.tsx's retry policy) can tell it apart from network/API
 *  failures. */
export class ParseError extends Error {
  readonly zodError: unknown;
  constructor(message: string, zodError?: unknown) {
    super(message);
    this.name = "ParseError";
    this.zodError = zodError;
  }
}

/** Mark a parser STRICT: a mismatch throws `ParseError` into the query error
 *  path instead of passing the raw payload through. Use ONLY for the
 *  collections pages `.map()` over (rule 1 in the file header).
 *
 *  Generic over the zod schema (not LooseParser<T>) so T is taken from
 *  `z.output<S>` directly — inferring T structurally from `safeParse` drags
 *  the failure branch's absent `data` into the type as `| undefined`. */
export function strictParser<S extends z.ZodType>(parser: S): LooseParser<z.output<S>> {
  return {
    strictParse: true,
    safeParse: (data: unknown) => parser.safeParse(data),
  };
}

/** Validate a response. Strict parsers throw `ParseError` on mismatch;
 *  lenient parsers fall back to the raw payload (and warn in dev). */
export function parseWith<T>(raw: unknown, parser: LooseParser<T>): T {
  const out = parser.safeParse(raw);
  if (out.success) return out.data;
  if (parser.strictParse) {
    throw new ParseError("Response did not match the expected shape.", out.error);
  }
  if (process.env.NODE_ENV !== "production") {
    console.warn("[api] response did not match its schema — rendering it as-is.", out.error);
  }
  return raw as T;
}

/* ------------------------------------------------------------------ auth */

export const authUserSchema = z.looseObject({
  id: z.string(),
  email: z.string(),
  full_name: z.string().nullable(),
  role: z.string(),
});

export const authResponseSchema = z.looseObject({
  token: z.string(),
  user: authUserSchema,
});

export const meResponseSchema = z.looseObject({
  user: authUserSchema,
});

/* ---------------------------------------------------------- notifications */

export const notificationItemSchema = z.looseObject({
  id: z.string(),
  type: z.string(),
  title: z.string(),
  body: z.string(),
  link: z.string().nullable(),
  read: z.boolean(),
  email_status: z.string(),
  created_at: z.string(),
});

export const notificationsResponseSchema = strictParser(
  z.looseObject({
    // Collection the bell and the notifications page `.map()` over → strict.
    items: z.array(notificationItemSchema),
    // Metadata (drives a badge only) → lenient, never blanks the list.
    unread_count: z.number().catch(0),
  })
);

/* -------------------------------------------------------------- strategies */

export const strategySummarySchema = z.looseObject({
  id: z.string(),
  status: z.string(),
  plan_health_score: z.string().nullable(),
  summary: z.string().nullable(),
  created_at: z.string(),
});

export const strategiesListSchema = strictParser(
  z.looseObject({
    items: z.array(strategySummarySchema),
  })
);

/** Worst OPEN risk attached to a portfolio card. */
export const topRiskSchema = z.looseObject({
  id: z.string(),
  severity: z.string(),
  risk_type: z.string(),
  title: z.string(),
});

export const evidenceFreshnessSchema = z.looseObject({
  status: z.string(),
  stale: z.number(),
  total: z.number(),
});

/** One GET /strategies/{id}/portfolio row (backend `_portfolio_rows`). */
export const portfolioRowSchema = z.looseObject({
  program_id: z.string(),
  program_name: z.string().nullable(),
  institution: z.string().nullable(),
  category: z.string(),
  priority: z.number(),
  rationale: z.string(),
  fit_score: z.string().nullable(),
  reasons: z.array(z.string()),
  top_risk: topRiskSchema.nullable(),
  estimated_cost: z.record(z.string(), z.unknown()),
  next_deadline: z.string().nullable(),
  next_action: z.string().nullable(),
  evidence_freshness: evidenceFreshnessSchema.nullable(),
});

export const portfolioListSchema = strictParser(
  z.looseObject({
    items: z.array(portfolioRowSchema),
  })
);

export const riskSchema = z.looseObject({
  id: z.string(),
  risk_type: z.string(),
  severity: z.string(),
  title: z.string(),
  reason: z.string(),
  recommended_action: z.string(),
  status: z.string(),
  confidence: z.string(),
  program_id: z.string().nullable(),
  requirement_id: z.string().nullable(),
  resolved_at: z.string().nullable(),
});

export const riskListSchema = strictParser(
  z.looseObject({
    items: z.array(riskSchema),
  })
);

export const roadmapTaskSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
  task_type: z.string(),
  status: z.string(),
  due_date: z.string().nullable(),
});

/** Strict on the three collections pages `.map()` over (portfolio, risks,
 *  roadmap_tasks); the display-only scalars use `.catch()` so one drifted
 *  counter never blanks a working plan. */
export const strategyDetailSchema = strictParser(
  z.looseObject({
    id: z.string(),
    status: z.string(),
    plan_health_score: z.string().nullable().catch(null),
    summary: z.string().nullable().catch(null),
    scoring_version: z.string().catch(""),
    strategy_version: z.string().catch(""),
    created_at: z.string().catch(""),
    portfolio: z.array(portfolioRowSchema),
    risks: z.array(riskSchema),
    roadmap_tasks: z.array(roadmapTaskSchema),
  })
);

/* ---------------------------------------------------------------- research */

export const researchRunOutSchema = z.looseObject({
  research_plan_id: z.string(),
  status: z.string(),
});

export const researchStepSchema = z.looseObject({
  step_key: z.string(),
  service_name: z.string(),
  status: z.string(),
  error_message: z.string().nullable(),
  output: z.record(z.string(), z.unknown()),
});

export const researchEventsSchema = strictParser(
  z.looseObject({
    run_id: z.string().catch(""),
    steps: z.array(researchStepSchema),
  })
);

export const researchRunSchema = z.looseObject({
  status: z.string(),
  error_message: z.string().nullable(),
  mode: z.enum(["live", "demo"]).optional(),
});

/* ----------------------------------------------------------------- monitor */

export const subscriptionSchema = z.looseObject({
  id: z.string(),
  field_key: z.string(),
  frequency: z.string(),
  enabled: z.boolean(),
  program_id: z.string().nullable().optional(),
  program_name: z.string().nullable().optional(),
  next_check_at: z.string().nullable().optional(),
  last_checked_at: z.string().nullable().optional(),
});

export const subscriptionsListSchema = strictParser(
  z.looseObject({
    items: z.array(subscriptionSchema),
  })
);

export const monitorCheckSchema = z.looseObject({
  id: z.string(),
  change_type: z.string(),
  material_change: z.boolean(),
  old_value: z.unknown(),
  new_value: z.unknown(),
  explanation: z.string().nullable().optional(),
});

export const monitorChangeSchema = z.looseObject({
  id: z.string(),
  change_type: z.string(),
  material_change: z.boolean(),
  checked_at: z.string(),
  old_value: z.unknown().optional(),
  new_value: z.unknown().optional(),
  explanation: z.string().nullable().optional(),
});

export const subscriptionChangesSchema = strictParser(
  z.looseObject({
    items: z.array(monitorChangeSchema),
  })
);

/* ------------------------------------------------------------------ fit */

export const fitItemSchema = z.looseObject({
  program_id: z.string(),
  overall_score: z.string(),
  explanation: z.string().nullable(),
});

export const fitResponseSchema = strictParser(
  z.looseObject({
    scoring_version: z.string().catch(""),
    items: z.array(fitItemSchema),
  })
);

/* --------------------------------------------------------------- profile */

/** GET /me/profile — form-fill data that is never `.map()`ped → lenient
 *  (rule 2): every field tolerates drift so a profile view never blanks. */
export const profileSchema = z.looseObject({
  id: z.string().catch(""),
  current_degree: z.string().nullable().catch(null),
  field_of_study: z.string().nullable().catch(null),
  institution_name: z.string().nullable().catch(null),
  institution_country_code: z.string().nullable().catch(null),
  graduation_year: z.number().nullable().catch(null),
  cgpa: z.string().nullable().catch(null),
  cgpa_scale: z.string().nullable().catch(null),
  percentage: z.string().nullable().catch(null),
  backlogs: z.number().nullable().catch(null),
  total_experience_months: z.number().nullable().catch(null),
  budget_currency: z.string().nullable().catch(null),
  total_budget_amount: z.string().nullable().catch(null),
  annual_budget_amount: z.string().nullable().catch(null),
  tuition_budget_amount: z.string().nullable().catch(null),
  scholarship_dependence: z.boolean().nullable().catch(null),
  career_goal: z.string().nullable().catch(null),
  profile_completion: z.string().nullable().catch(null),
  onboarding_version: z.string().catch(""),
});

/* --------------------------------------------------------------- programs */

export const programListItemSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  country_code: z.string().nullable(),
  degree_type: z.string().nullable(),
  field_of_study: z.string().nullable(),
  official_url: z.string().nullable(),
});

/** GET /programs list — `{items,total,page,page_size}` (PLAN.md cross-WS
 *  contract). `items` is strict (the explore page `.map()`s it); the paging
 *  counters are metadata → `.catch()` defaults so a drifted total never
 *  blanks a working list. */
export const programListSchema = strictParser(
  z.looseObject({
    items: z.array(programListItemSchema),
    total: z.number().catch(0),
    page: z.number().catch(1),
    page_size: z.number().catch(20),
  })
);

/* -------------------------------------------------------------- evidence */

/** Mirrors GET /evidence `items` (backend evidence.py serializer). */
export const evidenceItemSchema = z.looseObject({
  id: z.string(),
  claim_type: z.string(),
  claim: z.string(),
  normalized_claim: z.string().nullable(),
  confidence: z.string(),
  status: z.string(),
  retrieved_at: z.string().nullable(),
  freshness_deadline: z.string().nullable().optional(),
  subject_id: z.string().nullable(),
  source_url: z.string().nullable().optional(),
  source_domain: z.string().nullable().optional(),
  source_authority: z.string().nullable().optional(),
});

export const evidenceListSchema = strictParser(
  z.looseObject({
    items: z.array(evidenceItemSchema),
  })
);

/* ------------------------------------------------------------ onboarding */

/** One field definition from GET /onboarding/schema. `key`/`question`/
 *  `input_type` are what the wizard renders from → required; the copy
 *  around them (explanation, example, why-we-ask) is display metadata →
 *  lenient so a reworded backend never breaks the wizard. */
export const onboardingFieldSchema = z.looseObject({
  key: z.string(),
  question: z.string(),
  explanation: z.string().catch(""),
  input_type: z.string(),
  options: z.array(z.string()).optional(),
  optionLabels: z.record(z.string(), z.string()).optional(),
  required: z.boolean().optional(),
  example: z.string().optional(),
  why_we_ask: z.string().optional(),
});

/** One wizard screen (GET /onboarding/schema `steps[]`). */
export const onboardingStepSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
  fields: z.array(onboardingFieldSchema),
});

/** The wizard renders a screen per step and `.map()`s `fields` per screen →
 *  the steps collection is strict (ParseError → the wizard's Retry state). */
export const onboardingSchemaResponse = strictParser(
  z.looseObject({
    steps: z.array(onboardingStepSchema),
  })
);

export type OnboardingField = z.infer<typeof onboardingFieldSchema>;
export type OnboardingStep = z.infer<typeof onboardingStepSchema>;
