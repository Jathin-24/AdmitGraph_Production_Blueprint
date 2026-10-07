"use client";

import { useQuery } from "@tanstack/react-query";
import { getRun, getRunEvents, startResearchRun } from "../lib/api";
import { useState } from "react";

const STEP_LABELS: Record<string, string> = {
  validate_profile: "Understanding your profile",
  plan_queries: "Planning research",
  discovery_search: "Finding candidate programs",
  normalize_programs: "Organizing results",
  extract_evidence: "Verifying requirements",
  evaluate_requirements: "Checking requirements",
  score_fit: "Scoring fit",
  assess_risks: "Building risk profile",
  build_strategy: "Building strategy",
};

export default function ResearchPage() {
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const events = useQuery({
    queryKey: ["events", runId],
    queryFn: () => getRunEvents(runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const data = q.state.data;
      const done = data?.steps.every((s) => ["SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL"].includes(s.status));
      return done ? false : 1500;
    },
  });

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId!),
    enabled: !!runId,
    refetchInterval: 2000,
  });

  async function start() {
    setError(null);
    try {
      const out = await startResearchRun();
      setRunId(out.research_plan_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start research");
    }
  }

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-10">
      <h1 className="text-2xl font-semibold">Live research</h1>
      <p className="text-neutral-600">Powered by SerpApi. Progress reflects real backend events.</p>
      <button onClick={start} className="w-fit rounded-full bg-black px-5 py-2 text-white">
        Start research
      </button>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <ul className="flex flex-col gap-2" aria-live="polite">
        {(events.data?.steps ?? []).map((s) => (
          <li key={s.step_key} className="flex items-center gap-3">
            <span aria-hidden>{s.status === "SUCCEEDED" ? "✓" : s.status === "RUNNING" ? "●" : s.status === "FAILED" ? "✕" : "○"}</span>
            <span>{STEP_LABELS[s.step_key] ?? s.step_key}</span>
            {s.error_message && <span className="text-sm text-red-600">{s.error_message}</span>}
          </li>
        ))}
      </ul>
      {run.data && <p>Run status: {run.data.status}</p>}
    </main>
  );
}
