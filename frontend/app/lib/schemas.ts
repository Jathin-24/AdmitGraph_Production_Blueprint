/**
 * Zod schemas for the API reads we depend on (FRONTEND_SPEC §Frontend
 * architecture: "Zod for client validation").
 *
 * Every schema is deliberately LOOSE: unknown extra fields pass through
 * untouched, so a newer backend can add fields without breaking the UI.
 * Fields the UI reads are still typed and checked — but a mismatch never
 * throws. `parseWith` falls back to the raw payload (and warns in dev) so a
 * schema drift degrades to today's behaviour instead of a blank screen.
 */

import { z } from "zod";

/** Structural shape of a zod parser — keeps api.ts free of zod generics. */
export interface LooseParser<T> {
  safeParse(data: unknown): { success: true; data: T; error?: unknown } | { success: false; error?: unknown };
}

/** Validate a response, falling back to the raw payload on any mismatch. */
export function parseWith<T>(raw: unknown, parser: LooseParser<T>): T {
  const out = parser.safeParse(raw);
  if (out.success) return out.data;
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

export const notificationsResponseSchema = z.looseObject({
  items: z.array(notificationItemSchema),
  unread_count: z.number(),
});

/* -------------------------------------------------------------- strategies */

export const strategySummarySchema = z.looseObject({
  id: z.string(),
  status: z.string(),
  plan_health_score: z.string().nullable(),
  summary: z.string().nullable(),
  created_at: z.string(),
});

export const strategiesListSchema = z.looseObject({
  items: z.array(strategySummarySchema),
});

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

export const portfolioListSchema = z.looseObject({
  items: z.array(portfolioRowSchema),
});

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

export const riskListSchema = z.looseObject({
  items: z.array(riskSchema),
});

export const roadmapTaskSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
  task_type: z.string(),
  status: z.string(),
  due_date: z.string().nullable(),
});

export const strategyDetailSchema = z.looseObject({
  id: z.string(),
  status: z.string(),
  plan_health_score: z.string().nullable(),
  summary: z.string().nullable(),
  scoring_version: z.string(),
  strategy_version: z.string(),
  created_at: z.string(),
  portfolio: z.array(portfolioRowSchema),
  risks: z.array(riskSchema),
  roadmap_tasks: z.array(roadmapTaskSchema),
});

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

export const researchEventsSchema = z.looseObject({
  run_id: z.string(),
  steps: z.array(researchStepSchema),
});

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

export const subscriptionsListSchema = z.looseObject({
  items: z.array(subscriptionSchema),
});

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

export const subscriptionChangesSchema = z.looseObject({
  items: z.array(monitorChangeSchema),
});

/* ------------------------------------------------------------------ fit */

export const fitItemSchema = z.looseObject({
  program_id: z.string(),
  overall_score: z.string(),
  explanation: z.string().nullable(),
});

export const fitResponseSchema = z.looseObject({
  scoring_version: z.string(),
  items: z.array(fitItemSchema),
});
