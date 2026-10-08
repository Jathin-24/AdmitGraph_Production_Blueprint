"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
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
  const historyIds = subItems.slice(0, 12).map((s) => s.id);

  // One shared history query per subscription: feeds both the per-row history
  // panel and the "What changed" cards above.
  const historyResults = useQueries({
    queries: historyIds.map((id) => ({
      queryKey: ["subscription-changes", id],
      queryFn: () => getSubscriptionChanges(id),
    })),
  });
  const historyById = new Map(historyIds.map((id, i) => [id, historyResults[i]]));

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
  const historyLoading = historyResults.some((q) => q.isLoading);
  for (const sub of subItems) {
    const rows = historyById.get(sub.id)?.data?.items ?? [];
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
              No material changes yet — the last checks found nothing worth flagging.
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
                  {historyById.get(s.id)?.isLoading && (
                    <LoadingNote what="Loading history…" />
                  )}
                  <ul className="flex flex-col gap-2.5 text-xs text-ink-soft">
                    {(historyById.get(s.id)?.data?.items ?? []).map((c) => (
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
                    {(historyById.get(s.id)?.data?.items ?? []).length === 0 && (
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
