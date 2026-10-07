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
  subject_id: string | null;
}

export function getStrategies(): Promise<{ items: StrategySummary[] }> {
  return apiFetch("/strategies");
}

export function getStrategy(strategyId: string): Promise<StrategyDetail> {
  return apiFetch(`/strategies/${strategyId}`);
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
