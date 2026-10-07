"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ConfidencePill,
  EmptyState,
  LoadingNote,
  PageHeader,
  Section,
  StatusPill,
  fmtDate,
} from "../components/ui";
import {
  getStrategies,
  getStrategy,
  listEvidence,
  simulateStrategy,
  type EvidenceItem,
} from "../lib/api";

const CATEGORY_ORDER = ["REACH", "TARGET", "LOWER_RISK"];
const CATEGORY_COPY: Record<string, string> = {
  REACH: "Ambitious, worth trying",
  TARGET: "Strong match for your profile",
  LOWER_RISK: "Solid backups",
};
const SCENARIOS = [
  "TOP_3_REJECTED",
  "BUDGET_MINUS_25_PERCENT",
  "IELTS_LOWERED",
  "REMOVE_COUNTRY",
  "DEADLINE_MISSED",
];

function severityChip(severity: string): string {
  if (severity === "CRITICAL" || severity === "HIGH") return "chip-bad";
  if (severity === "MEDIUM") return "chip-warn";
  return "chip-neutral";
}

function EvidenceDrawer({ programId, onClose }: { programId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["evidence", programId],
    queryFn: () => listEvidence(programId),
  });
  return (
    <div className="card p-4" role="region" aria-label="Evidence">
      <div className="mb-3 flex items-center justify-between border-b border-line pb-2">
        <h3 className="display text-base font-medium">Evidence</h3>
        <button onClick={onClose} aria-label="Close evidence" className="btn-ghost btn-sm">
          Close
        </button>
      </div>
      {isLoading && <LoadingNote what="Loading evidence…" />}
      {!isLoading && (data?.items ?? []).length === 0 ? (
        <p className="text-sm text-ink-faint">
          No verified evidence yet for this program — claims appear after a live research run
          extracts them.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {(data?.items ?? []).map((e: EvidenceItem) => (
            <li key={e.id} className="py-2.5 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <ConfidencePill confidence={e.confidence} />
                <StatusPill status={e.status} />
              </div>
              <p className="mt-1.5 text-sm text-ink">{e.claim}</p>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
                {e.source_domain && <span>Source: {e.source_domain}</span>}
                {e.source_authority && <span>Authority: {e.source_authority}</span>}
                <span>Retrieved: {fmtDate(e.retrieved_at)}</span>
                {e.freshness_deadline && <span>Fresh until: {fmtDate(e.freshness_deadline)}</span>}
                {e.source_url && (
                  <a href={e.source_url} target="_blank" rel="noreferrer" className="link">
                    Open source ↗
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [simResult, setSimResult] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });
  const detail = useQuery({
    queryKey: ["strategy", selected],
    queryFn: () => getStrategy(selected!),
    enabled: !!selected,
  });

  const simulate = useMutation({
    mutationFn: () => simulateStrategy(selected!, scenario),
    onSuccess: (out) => {
      setSimResult(
        out.error ?? `Scenario ${out.scenario} simulated: ${JSON.stringify(out.modified_profile)}`
      );
      queryClient.invalidateQueries({ queryKey: ["strategy", selected] });
    },
    onError: (err) => setSimResult(err instanceof Error ? err.message : "Simulation failed"),
  });

  const items = useMemo(() => strategies.data?.items ?? [], [strategies.data]);

  // Auto-select the newest strategy so the dashboard is never a blank slate.
  useEffect(() => {
    if (!selected && items.length > 0) setSelected(items[0].id);
  }, [items, selected]);

  const active = items.find((s) => s.id === selected);

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Strategy"
        title="My Plan"
        lede="Your portfolio, risks and roadmap — each one traceable to the evidence it was built from."
        actions={
          <Link href="/research" className="btn-primary">
            New research run
          </Link>
        }
      />

      {strategies.isLoading && <LoadingNote what="Loading strategies…" />}

      {strategies.isSuccess && items.length === 0 && (
        <EmptyState
          title="No strategies yet"
          body="Run live research once and your portfolio, risks and roadmap appear here."
          action={
            <Link href="/research" className="btn-primary btn-sm">
              Run live research
            </Link>
          }
        />
      )}

      {/* Strategy selector */}
      {items.length > 0 && (
        <nav className="flex flex-wrap gap-2" aria-label="Strategies">
          {items.map((s) => (
            <button
              key={s.id}
              onClick={() => {
                setSelected(s.id);
                setEvidenceFor(null);
              }}
              aria-pressed={selected === s.id}
              className={selected === s.id ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            >
              {fmtDate(s.created_at)} · health {s.plan_health_score ?? "–"}
            </button>
          ))}
        </nav>
      )}

      {/* Health band */}
      {active && detail.data && (
        <section className="card flex flex-wrap items-center gap-6 p-5" aria-label="Plan health">
          <div>
            <p className="eyebrow mb-1">Plan health</p>
            <p className="display text-4xl font-medium tabular-nums text-forest">
              {active.plan_health_score ?? "–"}
              <span className="text-lg text-ink-faint"> / 100</span>
            </p>
          </div>
          {detail.data.summary && (
            <p className="max-w-2xl flex-1 border-l border-line pl-6 text-sm text-ink-soft">
              {detail.data.summary}
            </p>
          )}
          <span className="chip chip-neutral self-start">
            {detail.data.strategy_version} · scored {detail.data.scoring_version}
          </span>
        </section>
      )}

      {detail.data && (
        <>
          {/* Portfolio */}
          <section aria-label="Portfolio">
            <h2 className="display mb-3 text-lg font-medium">Portfolio</h2>
            <div className="grid gap-4 md:grid-cols-3">
              {CATEGORY_ORDER.map((cat) => (
                <div key={cat} className="card p-4">
                  <div className="mb-3 border-b border-line pb-2">
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-ink">
                      {cat.replace("_", " ")}
                    </h3>
                    <p className="text-xs text-ink-faint">{CATEGORY_COPY[cat]}</p>
                  </div>
                  <ul className="flex flex-col gap-4">
                    {detail.data.portfolio
                      .filter((p) => p.category === cat)
                      .map((p) => (
                        <li key={p.program_id}>
                          <div className="flex items-baseline gap-2">
                            <span className="display text-sm font-medium text-forest">
                              {p.priority}
                            </span>
                            <Link
                              href={`/programs/${p.program_id}`}
                              className="display text-sm font-medium text-ink decoration-forest underline-offset-4 hover:underline"
                            >
                              {p.program_name ?? "Program"}
                            </Link>
                          </div>
                          <div className="text-xs text-ink-soft">{p.institution}</div>
                          <div className="mt-1 text-xs leading-relaxed text-ink-faint">
                            {p.rationale}
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs">
                            <button
                              className="link text-ink-soft"
                              onClick={() =>
                                setEvidenceFor(evidenceFor === p.program_id ? null : p.program_id)
                              }
                              aria-expanded={evidenceFor === p.program_id}
                            >
                              Why this recommendation?
                            </button>
                            {p.next_action && (
                              <span className="text-ink-faint">→ {p.next_action}</span>
                            )}
                          </div>
                        </li>
                      ))}
                    {detail.data.portfolio.filter((p) => p.category === cat).length === 0 && (
                      <li className="text-sm text-ink-faint">None in this tier.</li>
                    )}
                  </ul>
                </div>
              ))}
            </div>
          </section>

          {evidenceFor && (
            <EvidenceDrawer programId={evidenceFor} onClose={() => setEvidenceFor(null)} />
          )}

          {/* Risks */}
          <Section index="—" title="Risks">
            {detail.data.risks.length === 0 ? (
              <p className="text-sm text-ink-faint">No risks flagged.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {detail.data.risks.map((r) => (
                  <li key={r.id} className="border-l-4 border-line-dark pl-3 text-sm">
                    <span className={`chip ${severityChip(r.severity)} mr-2`}>{r.severity}</span>
                    <span className="font-medium text-ink">{r.title}</span>
                    <p className="mt-1 text-ink-soft">{r.reason}</p>
                    <p className="mt-0.5 text-xs text-ink-faint">→ {r.recommended_action}</p>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* Roadmap */}
          <Section index="—" title="Next tasks">
            <ul className="flex flex-col divide-y divide-line text-sm">
              {detail.data.roadmap_tasks.map((t) => (
                <li key={t.id} className="flex items-baseline gap-3 py-2 first:pt-0 last:pb-0">
                  <span aria-hidden className="text-forest">
                    ○
                  </span>
                  <span className="flex-1 text-ink">{t.title}</span>
                  {t.due_date && (
                    <span className="text-xs tabular-nums text-ink-faint">due {t.due_date}</span>
                  )}
                </li>
              ))}
              {detail.data.roadmap_tasks.length === 0 && (
                <li className="py-2 text-ink-faint">No tasks generated yet.</li>
              )}
            </ul>
          </Section>

          {/* Simulator */}
          <Section index="—" title="What-if simulator">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex flex-col gap-1">
                <span className="label">Scenario</span>
                <select
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value)}
                  className="field w-auto"
                >
                  {SCENARIOS.map((s) => (
                    <option key={s} value={s}>
                      {s.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => simulate.mutate()}
                disabled={simulate.isPending}
                className="btn-primary self-end"
              >
                {simulate.isPending ? "Simulating…" : "Simulate"}
              </button>
            </div>
            {simResult && (
              <pre className="mt-3 overflow-x-auto rounded-md bg-paper-dark p-3 text-xs text-ink-soft">
                {simResult}
              </pre>
            )}
            <p className="mt-2 text-xs text-ink-faint">
              Simulations recompute fit against your profile — they never predict admission
              outcomes.
            </p>
          </Section>
        </>
      )}
    </main>
  );
}
