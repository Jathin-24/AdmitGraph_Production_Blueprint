"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
      className={`mt-2 rounded border p-3 text-sm ${
        result.material_change ? "border-orange-200 bg-orange-50" : "border-neutral-200 bg-neutral-50"
      }`}
      role="status"
    >
      <p className="font-medium">
        {CHANGE_COPY[result.change_type] ?? result.change_type}
        {result.material_change && (
          <span className="ml-2 rounded bg-orange-100 px-1.5 py-0.5 text-xs text-orange-700">
            Material change
          </span>
        )}
      </p>
      {result.material_change && (
        <p className="mt-1 text-xs text-neutral-600">
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
  const programs = useQuery({ queryKey: ["programs", "for-monitor"], queryFn: () => listPrograms(1, 50) });

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
    onSuccess: (out, subscriptionId) => setResults((prev) => ({ ...prev, [subscriptionId]: out })),
  });

  const history = useQuery({
    queryKey: ["subscription-changes", openHistory],
    queryFn: () => getSubscriptionChanges(openHistory!),
    enabled: !!openHistory,
  });

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">Your plan is alive.</h1>
      <p className="mt-1 mb-6 text-sm text-neutral-500">
        Monitoring re-checks live sources and records only material changes — requirement changed,
        deadline changed, conflicting information, source became stale. Checks run on demand here;
        scheduled checks follow each subscription&apos;s frequency.
      </p>

      <section className="mb-6 rounded-xl border border-neutral-200 p-4" aria-label="Create monitor">
        <h2 className="mb-3 font-medium">Watch something</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            What to watch
            <select
              className="rounded border p-2"
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
          <label className="flex flex-col gap-1 text-sm">
            Frequency
            <select
              className="rounded border p-2"
              value={frequency}
              onChange={(e) => setFrequency(e.target.value)}
            >
              <option value="DAILY">Daily</option>
              <option value="WEEKLY">Weekly</option>
              <option value="MONTHLY">Monthly</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Program (optional)
            <select className="rounded border p-2" value={programId} onChange={(e) => setProgramId(e.target.value)}>
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
            className="rounded-full bg-black px-5 py-2 text-sm text-white"
          >
            Start watching
          </button>
        </div>
        {create.isError && (
          <p className="mt-2 text-sm text-red-600" role="alert">
            {(create.error as Error).message}
          </p>
        )}
        {create.isSuccess && (
          <p className="mt-2 text-sm text-green-700" role="status">
            Subscription created.
          </p>
        )}
      </section>

      <section aria-label="Subscriptions">
        <h2 className="mb-3 font-medium">Active subscriptions</h2>
        {subscriptions.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
        {subscriptions.isSuccess && subscriptions.data.items.length === 0 && (
          <div className="rounded border border-dashed p-6 text-center text-sm text-neutral-500">
            Nothing watched yet — create a subscription above, or from a program&apos;s page.
          </div>
        )}
        <ul className="flex flex-col gap-3">
          {(subscriptions.data?.items ?? []).map((s) => (
            <li key={s.id} className="rounded-xl border border-neutral-200 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm">
                  <span className="font-medium">
                    {FIELD_OPTIONS.find((f) => f.value === s.field_key)?.label ?? s.field_key}
                  </span>
                  <span className="ml-2 text-neutral-500">
                    · {s.frequency.toLowerCase()} · {s.enabled ? "enabled" : "paused"}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => runCheck.mutate(s.id)}
                    disabled={runCheck.isPending}
                    className="rounded-full border px-3 py-1 text-xs hover:border-black"
                  >
                    {runCheck.isPending ? "Checking…" : "Check now"}
                  </button>
                  <button
                    onClick={() => setOpenHistory(openHistory === s.id ? null : s.id)}
                    className="rounded-full border px-3 py-1 text-xs hover:border-black"
                    aria-expanded={openHistory === s.id}
                  >
                    {openHistory === s.id ? "Hide history" : "History"}
                  </button>
                </div>
              </div>
              {results[s.id] && <CheckResult result={results[s.id]} />}
              {openHistory === s.id && (
                <div className="mt-2 border-t pt-2">
                  {history.isLoading && <p className="text-xs text-neutral-500">Loading history…</p>}
                  <ul className="flex flex-col gap-1 text-xs text-neutral-600">
                    {(history.data?.items ?? []).map((c) => (
                      <li key={c.id} className="flex items-center gap-2">
                        <span>{new Date(c.checked_at).toLocaleString()}</span>
                        <span className="rounded bg-neutral-100 px-1.5 py-0.5">
                          {CHANGE_COPY[c.change_type] ?? c.change_type}
                        </span>
                        {c.material_change && (
                          <span className="rounded bg-orange-100 px-1.5 py-0.5 text-orange-700">
                            material
                          </span>
                        )}
                      </li>
                    ))}
                    {(history.data?.items ?? []).length === 0 && (
                      <li>No checks recorded yet.</li>
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
