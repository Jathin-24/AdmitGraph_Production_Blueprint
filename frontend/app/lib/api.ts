const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

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
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
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

export function startResearchRun(intakeYear?: number): Promise<ResearchRunOut> {
  return apiFetch<ResearchRunOut>("/research/runs", {
    method: "POST",
    body: JSON.stringify({ intake_year: intakeYear }),
  });
}

export function getRunEvents(runId: string): Promise<ResearchEvents> {
  return apiFetch<ResearchEvents>(`/research/runs/${runId}/events`);
}

export function getRun(runId: string): Promise<{ status: string; error_message: string | null }> {
  return apiFetch(`/research/runs/${runId}`);
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
}

export interface MonitorCheck {
  id: string;
  change_type: string;
  material_change: boolean;
  old_value: unknown;
  new_value: unknown;
}

export interface MonitorChange {
  id: string;
  change_type: string;
  material_change: boolean;
  checked_at: string;
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

export function simulateStrategy(
  strategyId: string,
  scenario: string
): Promise<{ counterfactual_run_id?: string; scenario: string; modified_profile?: Record<string, unknown>; error?: string }> {
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
