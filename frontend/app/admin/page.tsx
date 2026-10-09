"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader, fmtDateTime } from "../components/ui";
import { ApiError } from "../lib/api";
import { getResearchRuns, getSearchUsage, type ResearchRunItem } from "../lib/api-extra";

/** Status → chip visual for research-run rows (raw value stays in the cell). */
const RUN_STATUS_CLS: Record<string, string> = {
  SUCCEEDED: "chip-good",
  SUCCESS: "chip-good",
  COMPLETED: "chip-good",
  FAILED: "chip-bad",
  CANCELLED: "chip-neutral",
  PARTIAL: "chip-warn",
  RUNNING: "chip-warn",
  QUEUED: "chip-neutral",
  PENDING: "chip-neutral",
};

function runStatusCls(status: string): string {
  return RUN_STATUS_CLS[status] ?? "chip-neutral";
}

/** Wall-clock duration of a finished run, derived from its own timestamps. */
function runDuration(run: ResearchRunItem): string {
  if (!run.completed_at) return "—";
  const ms = Date.parse(run.completed_at) - Date.parse(run.created_at);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return "<1s";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** Error text truncated in the table; the full message stays on hover. */
function truncate(text: string, max = 140): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="display mt-1 text-2xl font-medium tabular-nums text-ink">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}

export default function AdminPage() {
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;

  const usage = useQuery({
    queryKey: ["admin-search-usage"],
    queryFn: getSearchUsage,
    retry: (failureCount, error) => !isForbidden(error) && failureCount < 2,
  });
  const runs = useQuery({
    queryKey: ["admin-research-runs", page, PAGE_SIZE],
    queryFn: () => getResearchRuns(page, PAGE_SIZE),
    retry: (failureCount, error) => !isForbidden(error) && failureCount < 2,
  });

  const forbidden = isForbidden(usage.error) || isForbidden(runs.error);
  const loading = usage.isLoading || runs.isLoading;
  const failed =
    !forbidden && !loading && (usage.isError || runs.isError)
      ? ((runs.error ?? usage.error) as Error).message
      : null;
  const retryAll = () => {
    void usage.refetch();
    void runs.refetch();
  };

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Operations"
        title="Admin"
        lede="Search usage and research-run logs for this deployment — no provider keys or raw payloads, ever."
        actions={
          <Link href="/dashboard" className="btn-secondary">
            ← Back to my plan
          </Link>
        }
      />

      {/* 403 — the backend rejects non-admin callers (P0-2). */}
      {forbidden && (
        <div className="card p-6" role="region" aria-label="Admins only">
          <span className="chip chip-warn">Admins only</span>
          <p className="display mt-3 text-xl font-medium text-ink">
            This page is for administrator accounts
          </p>
          <p className="mt-1 max-w-xl text-sm text-ink-soft">
            Usage metrics and run logs require the ADMIN role. Sign in with an administrator
            account to view them — or head back to your plan, everything there works as usual.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link href="/login" className="btn-primary">
              Sign in
            </Link>
            <Link href="/dashboard" className="btn-secondary">
              Back to my plan
            </Link>
          </div>
        </div>
      )}

      {!forbidden && loading && <LoadingNote what="Loading admin data…" />}

      {!forbidden && failed && (
        <div className="space-y-3">
          <ErrorNote
            message={`Could not load admin data: ${failed}. The backend may be restarting — try again in a moment.`}
          />
          <button type="button" onClick={retryAll} className="btn-secondary btn-sm">
            Try again
          </button>
        </div>
      )}

      {!forbidden && !loading && !failed && usage.data && (
        <>
          {/* Usage summary */}
          <section aria-label="Search usage">
            <h2 className="display mb-3 text-lg font-medium">Search usage</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <StatCard
                label="Total searches"
                value={String(usage.data.total_searches)}
                hint="Every search recorded, cached or not"
              />
              <StatCard
                label="Successful"
                value={String(usage.data.successful_searches)}
                hint="Searches that returned results"
              />
              <StatCard
                label="Results found"
                value={String(usage.data.total_results)}
                hint="Rows extracted across searches"
              />
              <StatCard
                label="Cache hits"
                value={String(usage.data.cache_hits)}
                hint="Served without a provider call"
              />
              <StatCard
                label="Avg duration"
                value={
                  usage.data.avg_duration_ms === null ? "—" : `${usage.data.avg_duration_ms} ms`
                }
                hint="Mean over successful searches"
              />
            </div>
          </section>

          {/* Engine / status breakdowns */}
          <section aria-label="Breakdowns" className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wide text-ink">
                By engine
              </h3>
              {Object.keys(usage.data.by_engine).length === 0 ? (
                <p className="mt-1 text-sm text-ink-faint">No searches recorded yet.</p>
              ) : (
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {Object.entries(usage.data.by_engine).map(([engine, count]) => (
                    <span key={engine} className="chip chip-neutral">
                      {engine}: {count}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wide text-ink">
                By status
              </h3>
              {Object.keys(usage.data.by_status).length === 0 ? (
                <p className="mt-1 text-sm text-ink-faint">No statuses recorded yet.</p>
              ) : (
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {Object.entries(usage.data.by_status).map(([status, count]) => (
                    <span
                      key={status}
                      className={`chip ${status.toUpperCase().includes("FAIL") ? "chip-bad" : status.toUpperCase().includes("SUCCESS") ? "chip-good" : "chip-neutral"}`}
                    >
                      {status}: {count}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* Research runs */}
          <section aria-label="Research runs">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="display text-lg font-medium">Research runs</h2>
              {runs.data && (
                <span className="text-xs text-ink-faint">{runs.data.total} total</span>
              )}
            </div>

            {runs.isLoading && <LoadingNote what="Loading runs…" />}
            {runs.isError && (
              <ErrorNote
                message={`Could not load research runs: ${(runs.error as Error).message}.`}
              />
            )}

            {runs.isSuccess && runs.data.items.length === 0 && (
              <EmptyState
                title="No research runs yet"
                body="Runs appear here as soon as someone starts research on this deployment."
              />
            )}

            {runs.isSuccess && runs.data.items.length > 0 && (
              <>
                <div className="overflow-x-auto">
                  <table className="table-editorial min-w-[720px]">
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>Created</th>
                        <th>Duration</th>
                        <th>Steps</th>
                        <th>Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.data.items.map((run) => (
                        <tr key={run.id}>
                          <td>
                            <span className={`chip ${runStatusCls(run.status)}`}>
                              {run.status}
                            </span>
                          </td>
                          <td className="whitespace-nowrap text-xs tabular-nums text-ink-soft">
                            {fmtDateTime(run.created_at)}
                            {run.completed_at && (
                              <span className="block text-ink-faint">
                                → {fmtDateTime(run.completed_at)}
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap text-xs tabular-nums text-ink-soft">
                            {runDuration(run)}
                          </td>
                          <td className="text-xs tabular-nums text-ink-soft">
                            {run.steps_failed > 0 && (
                              <span className="chip chip-bad mr-1.5">{run.steps_failed} failed</span>
                            )}
                            {run.steps_partial > 0 && (
                              <span className="chip chip-warn mr-1.5">
                                {run.steps_partial} partial
                              </span>
                            )}
                            {run.steps_failed === 0 && run.steps_partial === 0 && (
                              <span className="text-ink-faint">
                                {run.steps_total} steps
                              </span>
                            )}
                            {run.steps_failed + run.steps_partial > 0 && (
                              <span className="text-ink-faint">of {run.steps_total}</span>
                            )}
                          </td>
                          <td className="max-w-xs text-xs text-ink-soft">
                            {run.error_message ? (
                              <span title={run.error_message}>{truncate(run.error_message)}</span>
                            ) : (
                              <span className="text-ink-faint">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination — GET /admin/research-runs?page=&page_size= */}
                {(page > 1 || runs.data.next_cursor) && (
                  <div className="mt-3 flex items-center justify-between gap-3">
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                    >
                      ← Previous
                    </button>
                    <span className="text-xs tabular-nums text-ink-faint">
                      Page {runs.data.page} · {runs.data.items.length} of {runs.data.total}
                    </span>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      disabled={!runs.data.next_cursor}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      Next →
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}
    </main>
  );
}
