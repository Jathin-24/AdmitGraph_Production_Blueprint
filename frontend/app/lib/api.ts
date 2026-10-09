import {
  authResponseSchema,
  evidenceListSchema,
  fitResponseSchema,
  meResponseSchema,
  monitorCheckSchema,
  notificationsResponseSchema,
  onboardingSchemaResponse,
  parseWith,
  programListSchema,
  profileSchema,
  researchEventsSchema,
  researchRunOutSchema,
  researchRunSchema,
  strategiesListSchema,
  strategyDetailSchema,
  subscriptionChangesSchema,
  subscriptionsListSchema,
  type LooseParser,
  type OnboardingStep,
} from "./schemas";

/** Fallback for local development only. `NEXT_PUBLIC_API_BASE_URL` is inlined
 *  at BUILD time — an unset variable in a production build means every query
 *  would silently hit localhost, so `checkApiHealth()` + <ApiHealthBanner />
 *  surface that misconfiguration instead of "Cannot reach the server". */
const FALLBACK_API_BASE = "http://localhost:8000/api/v1";

if (!process.env.NEXT_PUBLIC_API_BASE_URL && process.env.NODE_ENV !== "production") {
  // One-time dev warning (module scope — evaluated once per session).
  console.warn(
    `[api] NEXT_PUBLIC_API_BASE_URL is not set — using the local fallback ${FALLBACK_API_BASE}. ` +
      "Set it before building for a real deployment."
  );
}

/** Base URL every request goes through. W5's api-extra.ts imports this. */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? FALLBACK_API_BASE;

export const TOKEN_KEY = "admitgraph_token";

/* ------------------------------------------------------------- errors */

/** Every non-2xx response, carrying the HTTP status and the backend's
 *  machine-readable code so callers can react precisely (409, 404, …)
 *  without string-matching human messages. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** 401 while a token was stored: the session is gone. The token is dropped
 *  and the user is sent to /login with the intended route preserved. */
export class SessionExpiredError extends ApiError {
  constructor() {
    super("Session expired, please sign in again.", 401, "UNAUTHORIZED");
    this.name = "SessionExpiredError";
  }
}

/** Path the session-expired redirect should return to after signing in. */
export function loginRedirectTarget(): string {
  if (typeof window === "undefined") return "/login";
  const next = `${window.location.pathname}${window.location.search}`;
  if (window.location.pathname.startsWith("/login") || window.location.pathname.startsWith("/register")) {
    return "/login";
  }
  return `/login?expired=1&next=${encodeURIComponent(next)}`;
}

/** Only one 401 ever triggers a navigation — parallel queries fail together. */
let redirectingToLogin = false;

function redirectIfSessionExpired(): void {
  if (typeof window === "undefined" || redirectingToLogin) return;
  const target = loginRedirectTarget();
  if (target === "/login") return; // already on the auth screen
  redirectingToLogin = true;
  window.location.assign(target);
}

/** Bearer token for authenticated requests; null = anonymous demo session. */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface ResearchRunOut {
  research_plan_id: string;
  status: string;
}

export interface ResearchStep {
  step_key: string;
  service_name: string;
  status: string;
  error_message: string | null;
  output: Record<string, unknown>;
}

export interface ResearchEvents {
  run_id: string;
  steps: ResearchStep[];
}

/** Default ceiling for a single API call. A request that never settles (for
 *  example a browser holding keep-alive sockets to a backend that has since
 *  restarted) must surface as a typed error the UI can render with Retry —
 *  never an endless "Loading…" spinner. */
const REQUEST_TIMEOUT_MS = 20_000;

/** Authenticated JSON client — W5 imports this from api.ts (PLAN.md
 *  cross-WS contract). Every request carries the stored bearer token; never
 *  call bare `fetch()` for API reads or a signed-in user silently reads the
 *  anonymous demo profile (P0-3). */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  parser?: LooseParser<T>,
  timeoutMs: number = REQUEST_TIMEOUT_MS
): Promise<T> {
  const hadToken = getToken() !== null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      // A caller-supplied signal keeps control; otherwise the timeout owns it.
      signal: init?.signal ?? controller.signal,
      // Merge instead of letting `...init` replace the whole headers object:
      // defaults < caller headers < auth, so passing init.headers can no
      // longer drop the Authorization header, and the auth token always wins.
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
        ...authHeaders(),
      },
    });
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(
        `The API at ${API_BASE} did not answer within ${Math.round(timeoutMs / 1000)}s. ` +
          "It may be restarting — try again.",
        0,
        "TIMEOUT"
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let code: string | null = null;
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      code = typeof body?.error?.code === "string" ? body.error.code : null;
    } catch {
      /* ignore */
    }
    if (res.status === 401 && hadToken) {
      // A 401 with a stored token means the session expired: drop the token
      // so the app falls back to the anonymous demo session, tell the user in
      // plain words and return them to the sign-in screen with their route.
      setToken(null);
      redirectIfSessionExpired();
      throw new SessionExpiredError();
    }
    throw new ApiError(message, res.status, code);
  }
  const json: unknown = await res.json();
  // Strict parsers (collections pages `.map()` over) throw a typed
  // ParseError into react-query's error path on mismatch; lenient parsers
  // degrade to the raw payload with a dev warning. See schemas.ts header.
  return parser ? parseWith(json, parser) : (json as T);
}

/* --------------------------------------------------------------- health */

/** Lightweight startup self-check: GET {API_BASE}/health. Returns the HTTP
 *  status, or null when nothing answered (unreachable / CORS / wrong base).
 *  A non-2xx answer with a real response usually means API_BASE points at
 *  the wrong server — both cases feed <ApiHealthBanner />. */
export async function checkApiHealth(timeoutMs = 5000): Promise<number | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store", signal: controller.signal });
    return res.status;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------------ auth */

export interface AuthUser {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
}

export interface AuthResponse {
  token: string;
  user: AuthUser;
}

export interface RegisterInput {
  email: string;
  password: string;
  full_name?: string;
}

export function login(input: { email: string; password: string }): Promise<AuthResponse> {
  return apiFetch<AuthResponse>(
    "/auth/login",
    { method: "POST", body: JSON.stringify(input) },
    authResponseSchema
  );
}

export function register(input: RegisterInput): Promise<AuthResponse> {
  return apiFetch<AuthResponse>(
    "/auth/register",
    { method: "POST", body: JSON.stringify(input) },
    authResponseSchema
  );
}

/** Current user for the stored bearer token; 401 clears it (see apiFetch). */
export function getMe(): Promise<{ user: AuthUser }> {
  return apiFetch<{ user: AuthUser }>("/auth/me", undefined, meResponseSchema);
}

export function startResearchRun(intakeYear?: number): Promise<ResearchRunOut> {
  return apiFetch<ResearchRunOut>(
    "/research/runs",
    { method: "POST", body: JSON.stringify({ intake_year: intakeYear }) },
    researchRunOutSchema
  );
}

export function getRunEvents(runId: string): Promise<ResearchEvents> {
  return apiFetch<ResearchEvents>(`/research/runs/${runId}/events`, undefined, researchEventsSchema);
}

export function getRun(runId: string): Promise<{
  status: string;
  error_message: string | null;
  mode?: "live" | "demo";
}> {
  return apiFetch<{ status: string; error_message: string | null; mode?: "live" | "demo" }>(
    `/research/runs/${runId}`,
    undefined,
    researchRunSchema
  );
}

/** Instant example run: replays a captured real research run (no credits burned). */
export function startDemoRun(): Promise<ResearchRunOut> {
  return apiFetch<ResearchRunOut>(
    "/research/demo",
    { method: "POST", body: "{}" },
    researchRunOutSchema
  );
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  body: string;
  link: string | null;
  read: boolean;
  email_status: string;
  created_at: string;
}

export function listNotifications(): Promise<{
  items: NotificationItem[];
  unread_count: number;
}> {
  return apiFetch<{ items: NotificationItem[]; unread_count: number }>(
    "/notifications",
    undefined,
    notificationsResponseSchema
  );
}

export function markNotificationRead(id: string): Promise<{ read: boolean }> {
  return apiFetch(`/notifications/${id}/read`, { method: "POST", body: "{}" });
}

export function markAllNotificationsRead(): Promise<{ marked: number }> {
  return apiFetch("/notifications/read-all", { method: "POST", body: "{}" });
}

/** Fetch a generated PDF (binary — bypasses the JSON apiFetch). */
export async function exportStrategyPdf(strategyId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/strategies/${strategyId}/export/pdf`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  return res.blob();
}

export interface StrategySummary {
  id: string;
  status: string;
  plan_health_score: string | null;
  summary: string | null;
  created_at: string;
}

/** FRONTEND_SPEC §Strategy dashboard card fields (backend `_portfolio_rows`). */
export interface PortfolioRow {
  program_id: string;
  program_name: string | null;
  institution: string | null;
  category: string;
  priority: number;
  rationale: string;
  fit_score: string | null;
  reasons: string[];
  top_risk: { id: string; severity: string; risk_type: string; title: string } | null;
  estimated_cost: Record<string, unknown>;
  next_action: string | null;
  next_deadline: string | null;
  evidence_freshness: { status: string; stale: number; total: number } | null;
}

export interface StrategyRisk {
  id: string;
  risk_type: string;
  severity: string;
  title: string;
  reason: string;
  recommended_action: string;
  status: string;
  confidence: string;
  program_id: string | null;
  requirement_id: string | null;
  resolved_at: string | null;
}

export interface StrategyDetail {
  id: string;
  status: string;
  plan_health_score: string | null;
  summary: string | null;
  scoring_version: string;
  strategy_version: string;
  created_at: string;
  portfolio: PortfolioRow[];
  risks: StrategyRisk[];
  roadmap_tasks: {
    id: string;
    title: string;
    task_type: string;
    status: string;
    due_date: string | null;
  }[];
}

export interface EvidenceItem {
  id: string;
  claim_type: string;
  claim: string;
  normalized_claim: string | null;
  confidence: string;
  status: string;
  retrieved_at: string | null;
  freshness_deadline?: string | null;
  subject_id: string | null;
  source_url?: string | null;
  source_domain?: string | null;
  source_authority?: string | null;
}

export interface ProgramListItem {
  id: string;
  name: string;
  country_code: string | null;
  degree_type: string | null;
  field_of_study: string | null;
  official_url: string | null;
}

export interface ProgramDetail extends ProgramListItem {
  institution: string | null;
  institution_domain: string | null;
  city: string | null;
  specialization: string | null;
  language: string | null;
  duration_months: number | null;
  tuition_amount: string | null;
  tuition_currency: string | null;
  active: boolean;
  last_verified_at: string | null;
  requirement_count: number;
  evidence_count: number;
}

export interface RequirementItem {
  id: string;
  requirement_type: string;
  title: string;
  normalized_key: string;
  operator: string;
  value: Record<string, unknown>;
  mandatory: boolean;
  status: string;
  last_verified_at: string | null;
  evidence_ids?: string[];
}

export interface ProfileOut {
  id: string;
  current_degree: string | null;
  field_of_study: string | null;
  institution_name: string | null;
  institution_country_code: string | null;
  graduation_year: number | null;
  cgpa: string | null;
  cgpa_scale: string | null;
  percentage: string | null;
  backlogs: number | null;
  total_experience_months: number | null;
  budget_currency: string | null;
  total_budget_amount: string | null;
  annual_budget_amount: string | null;
  tuition_budget_amount: string | null;
  scholarship_dependence: boolean | null;
  career_goal: string | null;
  profile_completion: string | null;
  onboarding_version: string;
}

export interface CompletionOut {
  profile_completion: number;
  missing_fields: string[];
}

export interface ValidationOut {
  valid: boolean;
  issues: { field: string; message: string }[];
}

export interface Subscription {
  id: string;
  field_key: string;
  frequency: string;
  enabled: boolean;
  program_id?: string | null;
  program_name?: string | null;
  next_check_at?: string | null;
  last_checked_at?: string | null;
}

export interface MonitorCheck {
  id: string;
  change_type: string;
  material_change: boolean;
  old_value: unknown;
  new_value: unknown;
  explanation?: string | null;
}

export interface MonitorChange {
  id: string;
  change_type: string;
  material_change: boolean;
  checked_at: string;
  old_value?: unknown;
  new_value?: unknown;
  explanation?: string | null;
}

export interface FitItem {
  program_id: string;
  overall_score: string;
  explanation: string | null;
}

export function getStrategies(): Promise<{ items: StrategySummary[] }> {
  return apiFetch<{ items: StrategySummary[] }>("/strategies", undefined, strategiesListSchema);
}

export function getStrategy(strategyId: string): Promise<StrategyDetail> {
  return apiFetch<StrategyDetail>(`/strategies/${strategyId}`, undefined, strategyDetailSchema);
}

/** Move a risk to ACKNOWLEDGED / RESOLVED / DISMISSED (OPEN is never a target). */
export function updateRisk(
  strategyId: string,
  riskId: string,
  status: "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED"
): Promise<{ risk: StrategyRisk }> {
  return apiFetch(`/strategies/${strategyId}/risks/${riskId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function getFit(strategyId: string): Promise<{ scoring_version: string; items: FitItem[] }> {
  return apiFetch<{ scoring_version: string; items: FitItem[] }>(
    `/strategies/${strategyId}/fit`,
    undefined,
    fitResponseSchema
  );
}

/** Evidence rows (optionally scoped to one program). The `items` collection
 *  is parsed strictly — the evidence drawers `.map()` it (schemas.ts rule 1). */
export function listEvidence(programId?: string): Promise<{ items: EvidenceItem[] }> {
  return apiFetch<{ items: EvidenceItem[] }>(
    programId ? `/evidence?program_id=${programId}` : "/evidence",
    undefined,
    evidenceListSchema
  );
}

export interface PortfolioMove {
  program_id: string;
  program_name: string | null;
  category: string;
  priority: number;
}

export interface SimulationResult {
  counterfactual_run_id?: string;
  scenario: string;
  modified_profile?: Record<string, unknown>;
  portfolio_before?: PortfolioMove[];
  portfolio_after?: PortfolioMove[];
  delta?: {
    moved_up: PortfolioMove[];
    moved_down: PortfolioMove[];
    added: PortfolioMove[];
    removed: PortfolioMove[];
    summary: string;
  };
  error?: string;
}

export function simulateStrategy(
  strategyId: string,
  scenario: string,
  modifications?: Record<string, unknown>
): Promise<SimulationResult> {
  return apiFetch(`/strategies/${strategyId}/simulate`, {
    method: "POST",
    body: JSON.stringify(modifications ? { scenario, modifications } : { scenario }),
  });
}

/** GET /programs query params (PLAN.md cross-WS contract with W3):
 *  `q` substring over program/university/city, `country` and `degree_level`
 *  exact matches, `sort` one of fit_score|name|deadline, plus the paging
 *  basics. All optional — absent/empty values are simply omitted. */
export type ProgramSort = "fit_score" | "name" | "deadline";

export interface ProgramListParams {
  page?: number;
  page_size?: number;
  saved_only?: boolean;
  q?: string;
  country?: string;
  degree_level?: string;
  sort?: ProgramSort;
}

export interface ProgramListResponse {
  items: ProgramListItem[];
  total: number;
  page: number;
  page_size: number;
}

export function listPrograms(params: ProgramListParams): Promise<ProgramListResponse>;
/** Positional form kept for existing call sites (dashboard/monitor/programs). */
export function listPrograms(
  page?: number,
  pageSize?: number,
  savedOnly?: boolean
): Promise<ProgramListResponse>;
export function listPrograms(
  pageOrParams: number | ProgramListParams = 1,
  pageSize?: number,
  savedOnly?: boolean
): Promise<ProgramListResponse> {
  const params: ProgramListParams =
    typeof pageOrParams === "object"
      ? pageOrParams
      : { page: pageOrParams, page_size: pageSize, saved_only: savedOnly };
  const query = new URLSearchParams();
  if (params.page && params.page !== 1) query.set("page", String(params.page));
  if (params.page_size && params.page_size !== 20) query.set("page_size", String(params.page_size));
  if (params.saved_only) query.set("saved_only", "true");
  if (params.q) query.set("q", params.q);
  if (params.country) query.set("country", params.country);
  if (params.degree_level) query.set("degree_level", params.degree_level);
  if (params.sort && params.sort !== "fit_score") query.set("sort", params.sort);
  const qs = query.toString();
  return apiFetch<ProgramListResponse>(`/programs${qs ? `?${qs}` : ""}`, undefined, programListSchema);
}

export function getProgram(programId: string): Promise<ProgramDetail> {
  return apiFetch(`/programs/${programId}`);
}

export function getProgramRequirements(programId: string): Promise<{ items: RequirementItem[] }> {
  return apiFetch(`/programs/${programId}/requirements`);
}

export function saveProgram(programId: string): Promise<{ saved: boolean }> {
  return apiFetch(`/programs/${programId}/save`, { method: "POST" });
}

export function unsaveProgram(programId: string): Promise<{ saved: boolean }> {
  return apiFetch(`/programs/${programId}/save`, { method: "DELETE" });
}

export function getProfile(): Promise<ProfileOut> {
  return apiFetch<ProfileOut>("/me/profile", undefined, profileSchema);
}

export function getCompletion(): Promise<CompletionOut> {
  return apiFetch("/me/profile/completion");
}

export function updateProfile(patch: Record<string, unknown>): Promise<ProfileOut> {
  return apiFetch("/me/profile", { method: "PATCH", body: JSON.stringify(patch) });
}

export function validateProfile(): Promise<ValidationOut> {
  return apiFetch("/me/profile/validate", { method: "POST", body: JSON.stringify({}) });
}

export function listSubscriptions(): Promise<{ items: Subscription[] }> {
  return apiFetch<{ items: Subscription[] }>(
    "/monitor/subscriptions",
    undefined,
    subscriptionsListSchema
  );
}

export function createSubscription(input: {
  field_key: string;
  frequency: string;
  program_id?: string;
}): Promise<{ id: string }> {
  return apiFetch("/monitor/subscriptions", { method: "POST", body: JSON.stringify(input) });
}

export function checkSubscription(subscriptionId: string): Promise<MonitorCheck> {
  return apiFetch<MonitorCheck>(
    `/monitor/subscriptions/${subscriptionId}/check`,
    { method: "POST", body: JSON.stringify({}) },
    monitorCheckSchema
  );
}

export function getSubscriptionChanges(subscriptionId: string): Promise<{ items: MonitorChange[] }> {
  return apiFetch<{ items: MonitorChange[] }>(
    `/monitor/subscriptions/${subscriptionId}/changes`,
    undefined,
    subscriptionChangesSchema
  );
}

export interface DocumentItem {
  id: string;
  document_type: string;
  status: string;
  expires_at: string | null;
}

export function listDocuments(): Promise<{ items: DocumentItem[] }> {
  return apiFetch("/documents");
}

export function createDocument(
  documentType: string,
  notes?: string
): Promise<{ id: string; document_type: string; status: string }> {
  return apiFetch("/documents", {
    method: "POST",
    body: JSON.stringify({ document_type: documentType, notes }),
  });
}

export function updateDocument(
  id: string,
  status: string
): Promise<{ id: string; status: string }> {
  return apiFetch(`/documents/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
}

export interface EvidenceHealth {
  programs_total: number;
  programs_with_evidence: number;
  evidence_total: number;
  by_status: Record<string, number>;
  by_confidence: Record<string, number>;
  stale_count: number;
}

export function getEvidenceHealth(strategyId: string): Promise<EvidenceHealth> {
  return apiFetch(`/strategies/${strategyId}/evidence-health`);
}

export interface OnboardingProgress {
  completion_percent: number;
  answered_keys: string[];
  missing_required_keys: string[];
}

export function getOnboardingProgress(): Promise<OnboardingProgress> {
  return apiFetch("/onboarding/progress");
}

/** Guided-setup schema (GET /onboarding/schema) — routed through the
 *  authenticated client so a signed-in wizard is never prefilled from the
 *  anonymous demo profile (P0-3). */
export function getOnboardingSchema(): Promise<{ steps: OnboardingStep[] }> {
  return apiFetch<{ steps: OnboardingStep[] }>(
    "/onboarding/schema",
    undefined,
    onboardingSchemaResponse
  );
}

/** Answers may carry structured values (e.g. `subjects: [{name, credits}]`,
 *  dates as YYYY-MM-DD strings) — the backend accepts dict[str, Any]. */
export function saveOnboardingAnswers(
  answers: Record<string, unknown>
): Promise<{ accepted_keys: string[] }> {
  return apiFetch("/onboarding/answers", {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function cancelRun(runId: string): Promise<{ status: string }> {
  return apiFetch(`/research/runs/${runId}/cancel`, { method: "POST", body: "{}" });
}

/** One bounded re-check of a claim (1 search + extraction).
 *  409 PROVIDER_UNAVAILABLE when the search provider has no key configured. */
export function recheckEvidence(evidenceId: string): Promise<{
  id: string;
  status: string;
  recheck?: Record<string, unknown>;
}> {
  return apiFetch(`/evidence/${evidenceId}/recheck`, { method: "POST", body: "{}" });
}

/** Resolve a conflict group. Omitting `preferred_evidence_id` resolves to the
 *  authority-preferred member recorded at detection time. */
export function resolveConflict(
  conflictId: string,
  input?: { preferred_evidence_id?: string; reason?: string }
): Promise<{ id: string; resolution_status: string; resolved_at: string | null }> {
  return apiFetch(`/evidence/conflicts/${conflictId}/resolve`, {
    method: "POST",
    body: JSON.stringify(input ?? {}),
  });
}

export interface ConflictItem {
  id: string;
  reason?: string | null;
  description?: string | null;
  conflict_key?: string | null;
  evidence_ids?: string[];
  status?: string;
  resolution_status?: string | null;
  preferred_evidence_id?: string | null;
  resolution_reason?: string | null;
  resolved_at?: string | null;
}

/** Conflict payload has shipped as both {items:[...]} and {conflicts:[...]} —
 *  normalize so callers only ever see {items}. */
export async function getEvidenceConflicts(
  evidenceId: string
): Promise<{ items: ConflictItem[] }> {
  const raw = await apiFetch<Record<string, unknown>>(`/evidence/${evidenceId}/conflicts`);
  const list = (raw?.items ?? raw?.conflicts ?? []) as ConflictItem[];
  return { items: Array.isArray(list) ? list : [] };
}
