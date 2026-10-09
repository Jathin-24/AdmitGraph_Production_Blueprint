"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader, fmtDate, fmtDateTime } from "../components/ui";
import {
  checkSubscription,
  createSubscription,
  getSubscriptionChanges,
  listPrograms,
  listSubscriptions,
  type MonitorChange,
  type MonitorCheck,
} from "../lib/api";
import { isRouteUnavailable } from "../lib/api-extra";

const FIELD_OPTIONS = [
  { value: "deadline", label: "Deadline changed" },
  { value: "cost", label: "Tuition / cost changed" },
  { value: "academic", label: "Academic requirement changed" },
  { value: "prerequisite", label: "Prerequisite changed" },
  { value: "scholarship", label: "New scholarship signal" },
];

const CHANGE_COPY: Record<string, string> = {
  UPDATED: "Changed since last check",
  UNCHANGED: "No change",
  CREATED: "First observation recorded",
};

/* ------------------------------------------------------- monitoring cards */

type CardKind = "requirement" | "deadline" | "cost" | "scholarship" | "stale" | "conflict";

const CARD_COPY: Record<CardKind, { title: string; chip: string; fallback: string; rail: string }> = {
  requirement: {
    title: "Requirement changed",
    chip: "chip-warn",
    fallback: "A requirement on this program changed since the last check.",
    rail: "border-l-amberx",
  },
  deadline: {
    title: "Deadline changed",
    chip: "chip-warn",
    fallback: "An application deadline moved since the last check.",
    rail: "border-l-amberx",
  },
  cost: {
    title: "Cost changed",
    chip: "chip-warn",
    fallback: "Tuition or another cost figure changed since the last check.",
    rail: "border-l-amberx",
  },
  scholarship: {
    title: "New scholarship signal",
    chip: "chip-good",
    fallback: "A new funding or scholarship signal was found for this program.",
    rail: "border-l-forest",
  },
  stale: {
    title: "Source became stale",
    chip: "chip-warn",
    fallback: "A source passed its freshness window — the value may have changed.",
    rail: "border-l-amberx",
  },
  conflict: {
    title: "Conflicting information detected",
    chip: "chip-bad",
    fallback: "Two sources disagree about this value.",
    rail: "border-l-danger",
  },
};

/** Map an API change_type (+ the field watched) onto a monitoring card.
 *  Unknown types return null — they still show in history, just not as a card. */
function cardKind(changeType: string, fieldKey: string): CardKind | null {
  const t = changeType.toUpperCase();
  if (t.includes("SCHOLARSHIP")) return "scholarship";
  if (t.includes("DEADLINE")) return "deadline";
  if (t.includes("COST") || t.includes("TUITION")) return "cost";
  if (t.includes("STALE") || t.includes("FRESH")) return "stale";
  if (t.includes("CONFLICT")) return "conflict";
  if (t.includes("REQUIREMENT") || t.includes("ACADEMIC") || t.includes("PREREQUISITE")) {
    return "requirement";
  }
  if (t === "UPDATED" || t === "CREATED") {
    if (fieldKey === "deadline") return "deadline";
    if (fieldKey === "scholarship") return "scholarship";
    if (fieldKey === "cost") return "cost";
    return "requirement";
  }
  // change_type doubles as the watched field_key (e.g. "cost", "academic").
  if (fieldKey === "cost") return "cost";
  return null;
}

/** Compact, hydration-safe rendering of a monitored value (dates are sliced,
 *  never formatted through the Date object). */
function describeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") {
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return value.length >= 10 ? value.slice(0, 10) : value;
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(describeValue).join(", ");
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return "—";
    }
  }
  return String(value);
}

function ValueChange({ oldV, newV }: { oldV: unknown; newV: unknown }) {
  if (oldV === undefined && newV === undefined) return null;
  return (
    <p className="mt-1 text-xs text-ink-soft">
      Was: <span className="text-ink">{describeValue(oldV)}</span> → Now:{" "}
      <span className="text-ink">{describeValue(newV)}</span>
    </p>
  );
}

/** Honest copy for provider-side failures (FRONTEND_SPEC §Error states). */
function friendlyProviderError(raw: string): string {
  const m = raw.toLowerCase();
  if (m.includes("provider") || m.includes("serpapi") || m.includes("409") || m.includes("quota")) {
    return "The source provider is unavailable right now — wait a minute and run the check again. No credits were spent.";
  }
  if (m.includes("501") || m.includes("not implemented")) {
    return "This check isn’t available on this backend yet — nothing changed.";
  }
  return raw;
}

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
      {result.explanation && (
        <p className="mt-1 text-sm text-ink">{result.explanation}</p>
      )}
      {(result.old_value !== undefined || result.new_value !== undefined) && (
        <ValueChange oldV={result.old_value} newV={result.new_value} />
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

  const subItems = subscriptions.data?.items ?? [];

  /* N+1 guard — this used to fire up to 12 parallel history requests on every
   * mount. Now:
   *  - ONE shared query key per batch, so react-query dedupes identical
   *    in-flight requests (remounts, focus refetches, StrictMode double runs),
   *  - only subscriptions that have actually been checked can have history —
   *    never-checked rows are skipped entirely,
   *  - chunks of at most 3 concurrent requests via Promise.allSettled, so one
   *    failing subscription can't cancel the rest,
   *  - each result is also written to the per-id ["subscription-changes", id]
   *    cache, so the per-row History panel reads from cache instead of
   *    refetching (and the expanded row fetches on its own only when it fell
   *    outside the batch window).
   * Backend handoff: a batch endpoint `GET /monitor/history?ids=a,b,c` would
   * collapse this whole batch into a single request. */
  const HISTORY_CONCURRENCY = 3;
  const HISTORY_WINDOW = 12;
  const historyIds = subItems
    .filter((s) => s.last_checked_at)
    .slice(0, HISTORY_WINDOW)
    .map((s) => s.id);

  const historyBatch = useQuery({
    queryKey: ["subscription-changes-batch", historyIds.join("|")],
    enabled: historyIds.length > 0,
    staleTime: 60_000,
    queryFn: async () => {
      const map = new Map<string, MonitorChange[]>();
      let routeMissing = false;
      for (let i = 0; i < historyIds.length; i += HISTORY_CONCURRENCY) {
        const chunk = historyIds.slice(i, i + HISTORY_CONCURRENCY);
        const settled = await Promise.allSettled(
          chunk.map((id) => getSubscriptionChanges(id))
        );
        settled.forEach((result, index) => {
          const id = chunk[index];
          if (result.status === "fulfilled") {
            map.set(id, result.value.items);
            // Share the per-id cache key so row panels never refetch.
            queryClient.setQueryData(["subscription-changes", id], result.value);
          } else if (isRouteUnavailable(result.reason)) {
            routeMissing = true;
          }
          // Other per-id failures leave that subscription without cards —
          // the rest of the batch still renders.
        });
      }
      return { map, routeMissing };
    },
  });

  // An expanded subscription outside the batch window (13th+ checked row)
  // fetches its own history on demand — shared key, staleTime stops repeat
  // hits while the panel is open.
  const expandedSub = openHistory ? subItems.find((s) => s.id === openHistory) : undefined;
  const expandedInBatch = !!openHistory && historyIds.includes(openHistory);
  const expandedQuery = useQuery({
    queryKey: ["subscription-changes", openHistory ?? "none"],
    queryFn: () => getSubscriptionChanges(openHistory!),
    enabled: !!expandedSub?.last_checked_at && !expandedInBatch,
    staleTime: 5 * 60_000,
  });

  const historyFor = (id: string): MonitorChange[] =>
    historyBatch.data?.map.get(id) ??
    (id === openHistory ? (expandedQuery.data?.items ?? []) : []);
  const historyLoadingFor = (id: string): boolean =>
    historyBatch.isLoading || (id === openHistory && expandedQuery.isLoading);

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

  type CardRow = {
    key: string;
    kind: CardKind;
    change: MonitorChange;
    context: string;
  };
  const cards: CardRow[] = [];
  const historyLoading = historyBatch.isLoading;
  for (const sub of subItems) {
    const rows = historyFor(sub.id);
    const fieldLabel =
      FIELD_OPTIONS.find((f) => f.value === sub.field_key)?.label ?? sub.field_key;
    for (const change of rows) {
      if (!change.material_change && !change.explanation) continue;
      const kind = cardKind(change.change_type, sub.field_key);
      if (!kind) continue;
      cards.push({
        key: `${sub.id}-${change.id}`,
        kind,
        change,
        context: sub.program_name ? `${fieldLabel} · ${sub.program_name}` : fieldLabel,
      });
    }
  }
  cards.sort((a, b) => (a.change.checked_at < b.change.checked_at ? 1 : -1));
  const topCards = cards.slice(0, 6);

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Monitoring"
        title="Your plan is alive."
        lede="Monitoring re-checks live sources and records only material changes — requirement changed, deadline changed, cost changed, scholarship signals, conflicting information, source became stale. Scheduled checks follow each subscription's frequency; “Check now” runs one immediately."
      />

      {/* What changed — monitoring cards (FRONTEND_SPEC §Monitoring) */}
      {subItems.length > 0 && (
        <section aria-label="What changed">
          <h2 className="display mb-3 text-lg font-medium">What changed</h2>
          {historyLoading && cards.length === 0 && <LoadingNote what="Loading change history…" />}
          {!historyLoading && topCards.length === 0 && (
            <p className="text-sm text-ink-faint">
              {subItems.some((s) => s.last_checked_at)
                ? "No material changes yet — the last checks found nothing worth flagging."
                : "No checks have run yet — scheduled checks start recording changes here."}
            </p>
          )}
          {topCards.length > 0 && (
            <ul className="grid gap-3 sm:grid-cols-2">
              {topCards.map((card) => {
                const copy = CARD_COPY[card.kind];
                return (
                  <li key={card.key} className={`card border-l-4 p-4 ${copy.rail}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="display text-base font-medium text-ink">{copy.title}</p>
                      <span className={`chip ${copy.chip}`}>{card.context}</span>
                    </div>
                    <p className="mt-1.5 text-sm text-ink">
                      {card.change.explanation ?? copy.fallback}
                    </p>
                    <ValueChange oldV={card.change.old_value} newV={card.change.new_value} />
                    <p className="mt-1.5 text-xs tabular-nums text-ink-faint">
                      {fmtDateTime(card.change.checked_at)}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

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
        {create.isError && (
          <div className="mt-3">
            <ErrorNote message={friendlyProviderError((create.error as Error).message)} />
          </div>
        )}
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
        {subscriptions.isError && (
          <ErrorNote
            message={`Could not load subscriptions: ${(subscriptions.error as Error).message}. The backend may be restarting — try again in a moment.`}
          />
        )}
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
              <p className="mt-1.5 text-xs text-ink-faint">
                Checks run automatically — next check{" "}
                {s.next_check_at ? fmtDate(s.next_check_at) : "scheduled"}
                {s.last_checked_at
                  ? ` · last checked ${fmtDate(s.last_checked_at)}`
                  : " · not checked yet"}
                .
              </p>
              {results[s.id] && <CheckResult result={results[s.id]} />}
              {runCheck.isError && runCheck.variables === s.id && (
                <div className="mt-3">
                  <ErrorNote
                    message={friendlyProviderError((runCheck.error as Error).message)}
                  />
                </div>
              )}
              {openHistory === s.id && (
                <div className="mt-3 border-t border-line pt-3">
                  {historyLoadingFor(s.id) && <LoadingNote what="Loading history…" />}
                  <ul className="flex flex-col gap-2.5 text-xs text-ink-soft">
                    {historyFor(s.id).map((c) => (
                      <li key={c.id} className="border-b border-line pb-2 last:border-0 last:pb-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="tabular-nums">{fmtDateTime(c.checked_at)}</span>
                          <span className="chip chip-neutral">
                            {CHANGE_COPY[c.change_type] ?? c.change_type}
                          </span>
                          {c.material_change && <span className="chip chip-warn">material</span>}
                        </div>
                        {c.explanation && <p className="mt-1 text-sm text-ink">{c.explanation}</p>}
                        <ValueChange oldV={c.old_value} newV={c.new_value} />
                      </li>
                    ))}
                    {!historyLoadingFor(s.id) && historyFor(s.id).length === 0 && (
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
