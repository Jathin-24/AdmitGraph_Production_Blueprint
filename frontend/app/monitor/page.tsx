"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader, fmtDateTime } from "../components/ui";
import {
  checkSubscription,
  createSubscription,
  getSubscriptionChanges,
  listPrograms,
  listSubscriptions,
  type MonitorCheck,
} from "../lib/api";

const FIELD_OPTIONS = [
  { value: "deadline", label: "Deadline changed" },
  { value: "cost", label: "Tuition / cost changed" },
  { value: "academic", label: "Academic requirement changed" },
  { value: "prerequisite", label: "Prerequisite changed" },
];

const CHANGE_COPY: Record<string, string> = {
  UPDATED: "Changed since last check",
  UNCHANGED: "No change",
  CREATED: "First observation recorded",
};

function CheckResult({ result }: { result: MonitorCheck }) {
  return (
    <div
      className={`mt-3 rounded-md border px-3 py-2 text-sm ${
        result.material_change
          ? "border-amberx/40 bg-amberx-tint"
          : "border-line bg-paper/60"
      }`}
      role="status"
    >
      <p className="font-medium text-ink">
        {CHANGE_COPY[result.change_type] ?? result.change_type}
        {result.material_change && (
          <span className="chip chip-warn ml-2">Material change</span>
        )}
      </p>
      {result.material_change && (
        <p className="mt-1 text-xs text-ink-soft">
          Was: {JSON.stringify(result.old_value)} → Now: {JSON.stringify(result.new_value)}
        </p>
      )}
    </div>
  );
}

export default function MonitorPage() {
  const queryClient = useQueryClient();
  const [fieldKey, setFieldKey] = useState(FIELD_OPTIONS[0].value);
  const [frequency, setFrequency] = useState("WEEKLY");
  const [programId, setProgramId] = useState("");
  const [results, setResults] = useState<Record<string, MonitorCheck>>({});
  const [openHistory, setOpenHistory] = useState<string | null>(null);

  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: listSubscriptions });
  const programs = useQuery({
    queryKey: ["programs", "for-monitor"],
    queryFn: () => listPrograms(1, 50),
  });

  const create = useMutation({
    mutationFn: () =>
      createSubscription({
        field_key: fieldKey,
        frequency,
        ...(programId ? { program_id: programId } : {}),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["subscriptions"] }),
  });

  const runCheck = useMutation({
    mutationFn: (subscriptionId: string) => checkSubscription(subscriptionId),
    onSuccess: (out, subscriptionId) =>
      setResults((prev) => ({ ...prev, [subscriptionId]: out })),
  });

  const history = useQuery({
    queryKey: ["subscription-changes", openHistory],
    queryFn: () => getSubscriptionChanges(openHistory!),
    enabled: !!openHistory,
  });

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Monitoring"
        title="Your plan is alive."
        lede="Monitoring re-checks live sources and records only material changes — requirement changed, deadline changed, conflicting information, source became stale. Checks run on demand here; scheduled checks follow each subscription's frequency."
      />

      {/* Create */}
      <section className="card p-5" aria-label="Create monitor">
        <h2 className="display mb-4 border-b border-line pb-3 text-lg font-medium">
          Watch something
        </h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="label">What to watch</span>
            <select
              className="field w-auto"
              value={fieldKey}
              onChange={(e) => setFieldKey(e.target.value)}
            >
              {FIELD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label">Frequency</span>
            <select
              className="field w-auto"
              value={frequency}
              onChange={(e) => setFrequency(e.target.value)}
            >
              <option value="DAILY">Daily</option>
              <option value="WEEKLY">Weekly</option>
              <option value="MONTHLY">Monthly</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label">Program (optional)</span>
            <select
              className="field w-auto max-w-64"
              value={programId}
              onChange={(e) => setProgramId(e.target.value)}
            >
              <option value="">All programs</option>
              {(programs.data?.items ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="btn-primary"
          >
            Start watching
          </button>
        </div>
        {create.isError && <div className="mt-3"><ErrorNote message={(create.error as Error).message} /></div>}
        {create.isSuccess && (
          <p className="mt-3 text-sm text-forest" role="status">
            Subscription created.
          </p>
        )}
      </section>

      {/* Subscriptions */}
      <section aria-label="Subscriptions">
        <h2 className="display mb-3 text-lg font-medium">Active subscriptions</h2>
        {subscriptions.isLoading && <LoadingNote what="Loading subscriptions…" />}
        {subscriptions.isSuccess && subscriptions.data.items.length === 0 && (
          <EmptyState
            title="Nothing watched yet"
            body="Create a subscription above, or start watching from a program's page."
          />
        )}
        <ul className="flex flex-col gap-3">
          {(subscriptions.data?.items ?? []).map((s) => (
            <li key={s.id} className="card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm">
                  <span className="font-medium text-ink">
                    {FIELD_OPTIONS.find((f) => f.value === s.field_key)?.label ?? s.field_key}
                  </span>
                  <span className="text-ink-faint">
                    {" "}
                    · {s.frequency.toLowerCase()} · {s.enabled ? "enabled" : "paused"}
                    {s.program_name ? ` · ${s.program_name}` : " · all programs"}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => runCheck.mutate(s.id)}
                    disabled={runCheck.isPending}
                    className="btn-secondary btn-sm"
                  >
                    {runCheck.isPending ? "Checking…" : "Check now"}
                  </button>
                  <button
                    onClick={() => setOpenHistory(openHistory === s.id ? null : s.id)}
                    className="btn-ghost btn-sm"
                    aria-expanded={openHistory === s.id}
                  >
                    {openHistory === s.id ? "Hide history" : "History"}
                  </button>
                </div>
              </div>
              {results[s.id] && <CheckResult result={results[s.id]} />}
              {openHistory === s.id && (
                <div className="mt-3 border-t border-line pt-3">
                  {history.isLoading && <LoadingNote what="Loading history…" />}
                  <ul className="flex flex-col gap-1.5 text-xs text-ink-soft">
                    {(history.data?.items ?? []).map((c) => (
                      <li key={c.id} className="flex flex-wrap items-center gap-2">
                        <span className="tabular-nums">{fmtDateTime(c.checked_at)}</span>
                        <span className="chip chip-neutral">
                          {CHANGE_COPY[c.change_type] ?? c.change_type}
                        </span>
                        {c.material_change && <span className="chip chip-warn">material</span>}
                      </li>
                    ))}
                    {(history.data?.items ?? []).length === 0 && (
                      <li className="text-ink-faint">No checks recorded yet.</li>
                    )}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
