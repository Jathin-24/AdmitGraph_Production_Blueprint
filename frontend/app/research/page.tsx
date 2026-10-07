"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ErrorNote, PageHeader, fmtDate } from "../components/ui";
import { getRun, getRunEvents, startResearchRun } from "../lib/api";

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

const TERMINAL = ["SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL"];

function stepGlyph(status: string): { mark: string; cls: string } {
  if (status === "SUCCEEDED") return { mark: "✓", cls: "bg-forest text-white border-forest" };
  if (status === "RUNNING") return { mark: "●", cls: "bg-white text-forest border-forest" };
  if (status === "FAILED") return { mark: "✕", cls: "bg-danger text-white border-danger" };
  return { mark: "○", cls: "bg-white text-ink-faint border-line-dark" };
}

export default function ResearchPage() {
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const events = useQuery({
    queryKey: ["events", runId],
    queryFn: () => getRunEvents(runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const data = q.state.data;
      const done = data?.steps.every((s) => TERMINAL.includes(s.status));
      return done ? false : 1500;
    },
  });

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status && TERMINAL.includes(status) ? false : 2000;
    },
  });

  async function start() {
    setError(null);
    setStarting(true);
    try {
      const out = await startResearchRun();
      setRunId(out.research_plan_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start research");
    } finally {
      setStarting(false);
    }
  }

  const steps = events.data?.steps ?? [];
  const finished = steps.length > 0 && steps.every((s) => TERMINAL.includes(s.status));
  const succeeded = steps.filter((s) => s.status === "SUCCEEDED").length;
  const runStatus = run.data?.status;

  return (
    <main className="mx-auto max-w-2xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Live research"
        title="Watch the research run"
        lede="Powered by SerpApi — progress reflects real backend events, not a canned animation. Search runs happen on our server; no keys touch your browser."
        actions={
          <button onClick={start} disabled={starting} className="btn-primary">
            {starting ? "Starting…" : runId ? "Run again" : "Start research"}
          </button>
        }
      />

      {error && <ErrorNote message={error} />}
      {run.data && (
        <p className="text-sm text-ink-soft">
          Run <span className="tabular-nums text-ink-faint">{runId?.slice(0, 8)}</span> ·{" "}
          <span className="font-medium text-ink">{runStatus}</span>
          {finished && ` · ${succeeded}/${steps.length} steps succeeded`}
        </p>
      )}

      {/* Timeline */}
      <ol className="relative flex flex-col gap-0 border-l border-line pl-6" aria-live="polite">
        {(events.data?.steps ?? []).map((s) => {
          const glyph = stepGlyph(s.status);
          return (
            <li key={s.step_key} className="relative pb-6 last:pb-0">
              <span
                aria-hidden
                className={`absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border text-[11px] ${glyph.cls}`}
              >
                {glyph.mark}
              </span>
              <div className="flex flex-wrap items-baseline gap-x-3">
                <span
                  className={`text-sm font-medium ${
                    s.status === "FAILED" ? "text-danger" : "text-ink"
                  }`}
                >
                  {STEP_LABELS[s.step_key] ?? s.step_key}
                </span>
                <span className="text-[11px] uppercase tracking-wide text-ink-faint">
                  {s.status.toLowerCase()}
                </span>
              </div>
              {s.error_message && (
                <p className="mt-1 text-xs text-danger">{s.error_message}</p>
              )}
            </li>
          );
        })}
        {steps.length === 0 && (
          <li className="relative pb-0">
            <span
              aria-hidden
              className="absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border border-line-dark bg-white text-[11px] text-ink-faint"
            >
              ○
            </span>
            <p className="text-sm text-ink-faint">
              Press “Start research” — steps appear here as the backend completes them.
            </p>
          </li>
        )}
      </ol>

      {finished && (
        <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <p className="display text-base font-medium text-ink">
              Run complete — {succeeded} of {steps.length} steps succeeded.
            </p>
            <p className="text-xs text-ink-faint">Finished: {fmtDate(new Date().toISOString())}</p>
          </div>
          <Link href="/dashboard" className="btn-primary">
            Open My Plan →
          </Link>
        </div>
      )}

      {events.isError && (
        <ErrorNote
          message={`Could not fetch run events: ${(events.error as Error).message}. The run may have been interrupted — start a new one.`}
        />
      )}
    </main>
  );
}
