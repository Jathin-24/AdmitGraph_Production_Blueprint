const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export const TOKEN_KEY = "admitgraph_token";

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

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const hadToken = getToken() !== null;
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    // A 401 means our stored token is no longer valid: drop it so the app
    // falls back to the anonymous demo session instead of erroring forever.
    if (res.status === 401 && hadToken) setToken(null);
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
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
  return apiFetch<AuthResponse>("/auth/login", { method: "POST", body: JSON.stringify(input) });
}

export function register(input: RegisterInput): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/register", { method: "POST", body: JSON.stringify(input) });
}

/** Current user for the stored bearer token; 401 clears it (see apiFetch). */
export function getMe(): Promise<{ user: AuthUser }> {
  return apiFetch("/auth/me");
}

export function startResearchRun(intakeYear?: number): Promise<ResearchRunOut> {
  return apiFetch<ResearchRunOut>("/research/runs", {
    method: "POST",
    body: JSON.stringify({ intake_year: intakeYear }),
  });
}

export function getRunEvents(runId: string): Promise<ResearchEvents> {
  return apiFetch<ResearchEvents>(`/research/runs/${runId}/events`);
}

export function getRun(runId: string): Promise<{
  status: string;
  error_message: string | null;
  mode?: "live" | "demo";
}> {
  return apiFetch(`/research/runs/${runId}`);
}

/** Instant example run: replays a captured real research run (no credits burned). */
export function startDemoRun(): Promise<ResearchRunOut> {
  return apiFetch<ResearchRunOut>("/research/demo", { method: "POST", body: "{}" });
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
  return apiFetch("/notifications");
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

export interface StrategyDetail {
  id: string;
  status: string;
  plan_health_score: string | null;
  summary: string | null;
  scoring_version: string;
  strategy_version: string;
  created_at: string;
  portfolio: {
    program_id: string;
    program_name: string | null;
    institution: string | null;
    category: string;
    priority: number;
    rationale: string;
    next_action: string | null;
    next_deadline: string | null;
  }[];
  risks: {
    id: string;
    risk_type: string;
    severity: string;
    title: string;
    reason: string;
    recommended_action: string;
    status: string;
  }[];
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
  return apiFetch("/strategies");
}

export function getStrategy(strategyId: string): Promise<StrategyDetail> {
  return apiFetch(`/strategies/${strategyId}`);
}

export function getFit(strategyId: string): Promise<{ scoring_version: string; items: FitItem[] }> {
  return apiFetch(`/strategies/${strategyId}/fit`);
}

export function listEvidence(programId?: string): Promise<{ items: EvidenceItem[] }> {
  return apiFetch(programId ? `/evidence?program_id=${programId}` : "/evidence");
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

export function simulateStrategy(strategyId: string, scenario: string): Promise<SimulationResult> {
  return apiFetch(`/strategies/${strategyId}/simulate`, {
    method: "POST",
    body: JSON.stringify({ scenario }),
  });
}

export function listPrograms(
  page = 1,
  pageSize = 20,
  savedOnly = false
): Promise<{ items: ProgramListItem[]; page: number; page_size: number; total: number; next_cursor: string | null }> {
  const saved = savedOnly ? "&saved_only=true" : "";
  return apiFetch(`/programs?page=${page}&page_size=${pageSize}${saved}`);
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
  return apiFetch("/me/profile");
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
  return apiFetch("/monitor/subscriptions");
}

export function createSubscription(input: {
  field_key: string;
  frequency: string;
  program_id?: string;
}): Promise<{ id: string }> {
  return apiFetch("/monitor/subscriptions", { method: "POST", body: JSON.stringify(input) });
}

export function checkSubscription(subscriptionId: string): Promise<MonitorCheck> {
  return apiFetch(`/monitor/subscriptions/${subscriptionId}/check`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function getSubscriptionChanges(subscriptionId: string): Promise<{ items: MonitorChange[] }> {
  return apiFetch(`/monitor/subscriptions/${subscriptionId}/changes`);
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

export function saveOnboardingAnswers(
  answers: Record<string, string>
): Promise<{ accepted_keys: string[] }> {
  return apiFetch("/onboarding/answers", {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function cancelRun(runId: string): Promise<{ status: string }> {
  return apiFetch(`/research/runs/${runId}/cancel`, { method: "POST", body: "{}" });
}

export interface ConflictItem {
  id: string;
  reason?: string | null;
  description?: string | null;
  conflict_key?: string | null;
  evidence_ids?: string[];
  status?: string;
  resolution_status?: string | null;
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
