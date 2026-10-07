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
