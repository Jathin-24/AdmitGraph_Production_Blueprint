"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import {
  cancelRun,
  getCompletion,
  getRun,
  getRunEvents,
  startDemoRun,
  startResearchRun,
  type ResearchStep,
} from "../lib/api";

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

/** FRONTEND_SPEC §Research screen — the six stages a student sees. Several
 *  backend steps roll up into one stage; a stage is only marked complete when
 *  every backend event behind it reports SUCCEEDED (never faked). */
const STAGES: { label: string; steps: string[] }[] = [
  { label: "Understanding your profile", steps: ["validate_profile", "plan_queries"] },
  { label: "Finding candidate programs", steps: ["discovery_search", "normalize_programs"] },
  { label: "Verifying requirements", steps: ["extract_evidence"] },
  { label: "Checking costs and deadlines", steps: ["evaluate_requirements"] },
  { label: "Building risk profile", steps: ["score_fit", "assess_risks"] },
  { label: "Building strategy", steps: ["build_strategy"] },
];

type StageStatus = "pending" | "running" | "done" | "failed";

function stageStatuses(steps: ResearchStep[]): StageStatus[] {
  const byKey = new Map(steps.map((s) => [s.step_key, s.status]));
  return STAGES.map((stage) => {
    const observed = stage.steps.map((key) => byKey.get(key));
    if (observed.length === 0 || observed.every((s) => s === undefined)) return "pending";
    if (observed.some((s) => s === "FAILED")) return "failed";
    if (observed.every((s) => s === "SUCCEEDED")) return "done";
    if (observed.some((s) => s === "RUNNING" || s === "PARTIAL")) return "running";
    return "pending";
  });
}

const TERMINAL = ["SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL"];
const ACTIVE = ["QUEUED", "RUNNING"];

function stepGlyph(status: string): { mark: string; cls: string } {
  if (status === "SUCCEEDED") return { mark: "✓", cls: "bg-forest text-white border-forest" };
  if (status === "PARTIAL") return { mark: "!", cls: "bg-amberx text-white border-amberx" };
  if (status === "RUNNING") return { mark: "●", cls: "bg-white text-forest border-forest" };
  if (status === "FAILED") return { mark: "✕", cls: "bg-danger text-white border-danger" };
  return { mark: "○", cls: "bg-white text-ink-faint border-line-dark" };
}

function asCount(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return Number(value);
  }
  if (Array.isArray(value)) return value.length;
  return 0;
}

/** First key that actually reports a non-zero figure. */
function firstCount(out: Record<string, unknown>, keys: string[]): number {
  for (const key of keys) {
    const value = out[key];
    if (value === undefined || value === null) continue;
    const n = asCount(value);
    if (n > 0) return n;
  }
  return 0;
}

/** Run-level totals from step outputs. Different steps report these under
 *  different keys (searches_run / searches / results_stored / sources), so we
 *  prefer an explicit counter and only fall back to deriving the figure from
 *  the discovery step's search list — the same run is never counted twice. */
function researchTotals(steps: ResearchStep[]): { searches: number; sources: number } {
  let searches = 0;
  let sources = 0;
  for (const step of steps) {
    const out = step.output ?? {};

    const searchList = Array.isArray(out.searches) ? (out.searches as unknown[]) : [];
    const countedResults = searchList.reduce<number>((sum, entry) => {
      if (entry && typeof entry === "object" && "count" in entry) {
        return sum + asCount((entry as { count: unknown }).count);
      }
      return sum;
    }, 0);

    const stepSearches =
      firstCount(out, ["searches_run", "search_runs"]) ||
      (searchList.length > 0 ? searchList.length : 0);
    const stepSources =
      firstCount(out, ["sources", "sources_checked", "results_stored", "source_count"]) ||
      countedResults ||
      asCount(out.results);

    searches = Math.max(searches, stepSearches);
    sources = Math.max(sources, stepSources);
  }
  return { searches, sources };
}

/** Honest, student-friendly copy for run/start failures (FRONTEND_SPEC
 *  §Error states: never show "Something went wrong" alone). */
function friendlyError(raw: string): string {
  const m = raw.toLowerCase();
  if (m.includes("provider") || m.includes("serpapi") || m.includes("409") || m.includes("quota")) {
    return "The search provider is unavailable right now — wait a minute and try again. Nothing was charged for the failed attempt.";
  }
  if (m.includes("501") || m.includes("not implemented") || m.includes("not yet available")) {
    return "The example run isn’t available on this backend yet — use “Run fresh research” instead.";
  }
  return raw;
}

/** Start-button failures get copy that names the actual next step. */
function startError(raw: string, kind: "demo" | "live"): string {
  const m = raw.toLowerCase();
  const unavailable =
    m.includes("501") ||
    m.includes("404") ||
    m.includes("not found") ||
    m.includes("not implemented") ||
    m.includes("not yet available");
  if (unavailable) {
    return kind === "demo"
      ? "The example run isn’t available on this backend yet — use “Run fresh research” instead."
      : "Live research couldn’t start — this server doesn’t offer the research endpoint yet. Try again in a moment.";
  }
  return friendlyError(raw);
}

export default function ResearchPage() {
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<"demo" | "live" | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Restore an in-flight run after mount (client-only, so SSR markup matches).
  useEffect(() => {
    const stored = sessionStorage.getItem("research_run_id");
    if (stored) setRunId(stored);
  }, []);

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

  // "Missing profile data" guidance (FRONTEND_SPEC §Error states).
  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });

  async function start(kind: "demo" | "live") {
    setError(null);
    setStarting(kind);
    try {
      const out = kind === "demo" ? await startDemoRun() : await startResearchRun();
      // New runId ⇒ new query keys ⇒ both queries fetch the fresh run.
      setRunId(out.research_plan_id);
      sessionStorage.setItem("research_run_id", out.research_plan_id);
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Could not start research";
      setError(startError(raw, kind));
    } finally {
      setStarting(null);
    }
  }

  async function cancel() {
    if (!runId) return;
    setCancelling(true);
    try {
      await cancelRun(runId);
      await Promise.all([run.refetch(), events.refetch()]);
    } catch (e) {
      setError(friendlyError(e instanceof Error ? e.message : "Could not cancel the run"));
    } finally {
      setCancelling(false);
    }
  }

  const steps = events.data?.steps ?? [];
  const finished = steps.length > 0 && steps.every((s) => TERMINAL.includes(s.status));
  const succeeded = steps.filter((s) => s.status === "SUCCEEDED").length;
  const runStatus = run.data?.status ?? null;
  const isDemo = run.data?.mode === "demo";
  const runInFlight = !!runId && !run.isError && (runStatus === null || ACTIVE.includes(runStatus));
  const ctasDisabled = runInFlight || starting !== null;

  const totals = researchTotals(steps);
  const stageStates = stageStatuses(steps);
  const sourcesLine =
    totals.sources > 0 && totals.searches === 0
      ? isDemo
        ? `Replayed ${totals.sources} sources from a completed live run`
        : `${totals.sources} sources checked`
      : totals.sources > 0
        ? `${totals.sources} sources checked across ${totals.searches} searches`
        : totals.searches > 0
          ? `${totals.searches} searches run — sources are counted as results are stored`
          : null;

  const missingFields = completion.data?.missing_fields ?? [];
  const profileIncomplete =
    (completion.data?.profile_completion ?? 100) < 100 && missingFields.length > 0;

  return (
    <main className="mx-auto max-w-2xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Research"
        title="Watch the research run"
        lede="Real backend events, not a canned animation — live research is powered by SerpApi on our server, and no provider keys ever touch your browser."
      />

      {/* Start a run */}
      <section className="card p-5" aria-label="Start a research run">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => start("demo")}
            disabled={ctasDisabled}
            className="btn-primary"
          >
            {starting === "demo" ? "Starting example…" : "▶ Run the full example"}
          </button>
          <button
            type="button"
            onClick={() => start("live")}
            disabled={ctasDisabled}
            className="btn-secondary"
          >
            {starting === "live" ? "Starting…" : "Run fresh research"}
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-faint">
          Instant walkthrough with real sourced data — no search credits used.
        </p>
        <p className="mt-0.5 text-xs text-ink-faint">
          “Run fresh research” uses live search credits (powered by SerpApi).
        </p>
        {runInFlight && (
          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3">
            <span className="text-xs text-ink-soft" role="status">
              A run is in progress — new runs start when it finishes.
            </span>
            <button
              type="button"
              onClick={cancel}
              disabled={cancelling || !runStatus || !ACTIVE.includes(runStatus)}
              className="btn-ghost btn-sm"
            >
              {cancelling ? "Cancelling…" : "Cancel run"}
            </button>
          </div>
        )}
        {profileIncomplete && (
          <p className="mt-3 rounded-md border border-line bg-paper-dark/60 px-3 py-2 text-xs text-ink-soft">
            Some profile answers are still missing ({missingFields.slice(0, 3).join(", ")}), so
            research may stop early.{" "}
            <Link href="/onboarding" className="link">
              Finish guided setup →
            </Link>
          </p>
        )}
      </section>

      {error && <ErrorNote message={error} />}

      {run.isError && (
        <ErrorNote
          message={
            (run.error as Error).message.includes("(404)")
              ? "The previous run is no longer on this server — it may have been cleaned up. Start a new run below."
              : `Could not restore the previous run: ${friendlyError(
                  (run.error as Error).message
                )} Start a new run below.`
          }
        />
      )}

      {run.data && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm" aria-live="polite">
          <span className="text-sm text-ink-soft">
            Run <span className="tabular-nums text-ink-faint">{runId?.slice(0, 8)}</span> ·{" "}
            <span className="font-medium text-ink">{runStatus}</span>
            {finished && ` · ${succeeded}/${steps.length} steps succeeded`}
          </span>
          {isDemo && <span className="chip chip-warn">Example</span>}
          {runStatus === "PARTIAL" && (
            <span className="chip chip-warn">Partial results — some sources unavailable</span>
          )}
          {runStatus === "CANCELLED" && <span className="chip chip-neutral">Cancelled</span>}
          {sourcesLine && (
            <span className="text-xs text-ink-faint">{sourcesLine}</span>
          )}
        </div>
      )}

      {/* Six-stage progress — FRONTEND_SPEC §Research screen */}
      <section className="card p-5" aria-label="Research stages">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2">
          <h2 className="display text-base font-medium">Research stages</h2>
          <span className="text-xs text-ink-faint">Live research powered by SerpApi</span>
        </div>
        <ol className="flex flex-col gap-2.5">
          {STAGES.map((stage, i) => {
            const status = stageStates[i];
            const glyph =
              status === "done"
                ? { mark: "✓", cls: "bg-forest text-white border-forest" }
                : status === "running"
                  ? { mark: "●", cls: "bg-white text-forest border-forest" }
                  : status === "failed"
                    ? { mark: "✕", cls: "bg-danger text-white border-danger" }
                    : { mark: "○", cls: "bg-white text-ink-faint border-line-dark" };
            return (
              <li key={stage.label} className="flex items-center gap-3 text-sm">
                <span
                  aria-hidden
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${glyph.cls}`}
                >
                  {glyph.mark}
                </span>
                <span
                  className={
                    status === "done"
                      ? "text-ink"
                      : status === "running"
                        ? "font-medium text-forest"
                        : status === "failed"
                          ? "text-danger"
                          : "text-ink-faint"
                  }
                >
                  {stage.label}
                </span>
                <span className="sr-only">
                  {status === "done"
                    ? "completed"
                    : status === "running"
                      ? "in progress"
                      : status === "failed"
                        ? "failed"
                        : "not started"}
                </span>
              </li>
            );
          })}
        </ol>
        <p className="mt-3 border-t border-line pt-2 text-xs text-ink-faint">
          Stages only turn green when the backend reports the step finished — nothing here is
          simulated.
          {sourcesLine ? ` ${sourcesLine}.` : ""}
        </p>
      </section>

      {/* Per-step detail (backend events) */}
      <details className="card p-4">
        <summary className="cursor-pointer text-sm font-medium text-ink-soft">
          Step-by-step detail ({steps.length} backend events)
        </summary>
        <ol className="relative mt-3 flex flex-col gap-0 border-l border-line pl-6" aria-live="polite">
        {steps.map((s) => {
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
                <p className="mt-1 text-xs text-danger">{friendlyError(s.error_message)}</p>
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
              {runInFlight
                ? "Run queued — steps appear here as the backend completes them."
                : "Press “Run the full example” — steps appear here as the backend completes them."}
            </p>
          </li>
        )}
        {steps.length > 0 && !finished && (
          <li className="relative pb-0">
            <LoadingNote what="Waiting for the next step…" />
          </li>
        )}
        </ol>
      </details>

      {runStatus === "FAILED" && (
        <div className="card border-danger/30 p-4" role="alert">
          <p className="display text-base font-medium text-danger">This run didn’t finish.</p>
          <p className="mt-1 text-sm text-ink-soft">
            {run.data?.error_message
              ? friendlyError(run.data.error_message)
              : "The backend stopped the run before it produced results."}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => start(isDemo ? "demo" : "live")}
              disabled={ctasDisabled}
              className="btn-primary btn-sm"
            >
              Try again
            </button>
            <Link href="/dashboard" className="btn-ghost btn-sm">
              Open My Plan anyway
            </Link>
          </div>
        </div>
      )}

      {finished && runStatus !== "FAILED" && (
        <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <p className="display text-base font-medium text-ink">
              {runStatus === "PARTIAL"
                ? "Run finished with warnings — some sources were unavailable."
                : "Run complete —"}
              {runStatus !== "PARTIAL" && ` ${succeeded} of ${steps.length} steps succeeded.`}
            </p>
            {runStatus === "PARTIAL" && (
              <span className="chip chip-warn mt-1.5 inline-flex">
                Partial results — some sources unavailable
              </span>
            )}
            {sourcesLine && <p className="mt-1 text-xs text-ink-faint">{sourcesLine}</p>}
          </div>
          <Link href="/dashboard" className="btn-primary">
            Open My Plan →
          </Link>
        </div>
      )}

      {events.isError && runId && (
        <ErrorNote
          message={
            (events.error as Error).message.includes("(404)")
              ? "This run’s steps are no longer on the server — start a new run to see progress again."
              : `Could not fetch run events: ${friendlyError(
                  (events.error as Error).message
                )} The run may have been interrupted — start a new one.`
          }
        />
      )}
    </main>
  );
}
