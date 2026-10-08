"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ConfidencePill,
  EmptyState,
  ErrorNote,
  EvidenceStatusChip,
  LoadingNote,
  PageHeader,
  Section,
  fmtDate,
} from "../components/ui";
import {
  createDocument,
  exportStrategyPdf,
  getCompletion,
  getEvidenceHealth,
  getStrategies,
  getStrategy,
  listDocuments,
  listEvidence,
  simulateStrategy,
  updateDocument,
  type DocumentItem,
  type EvidenceItem,
  type PortfolioMove,
  type SimulationResult,
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

/* ------------------------------------------------ Application Readiness */

/** MASTER_SPEC §17 — the eight documents we track (backend stores free-text
 *  document_type, so each row matches by normalised alias). */
const DOC_TYPES: { key: string; label: string; aliases: string[] }[] = [
  { key: "transcript", label: "Academic transcript", aliases: ["transcript", "academic_transcript"] },
  { key: "passport", label: "Passport", aliases: ["passport"] },
  {
    key: "language_score",
    label: "Language score report",
    aliases: ["language_score", "ielts", "toefl", "pte", "language"],
  },
  { key: "cv", label: "CV / résumé", aliases: ["cv", "resume"] },
  {
    key: "sop",
    label: "Statement of purpose",
    aliases: ["sop", "statement_of_purpose", "motivation_letter"],
  },
  {
    key: "lors",
    label: "Recommendation letters",
    aliases: ["lors", "lor", "recommendation_letters", "recommendations", "letters_of_recommendation"],
  },
  { key: "portfolio", label: "Portfolio", aliases: ["portfolio"] },
  {
    key: "financial_proof",
    label: "Financial proof",
    aliases: ["financial_proof", "financial_documents", "bank_statement", "fund_proof"],
  },
];

const DOC_STATUS_COPY: Record<string, { label: string; cls: string }> = {
  DONE: { label: "Ready", cls: "chip-good" },
  IN_PROGRESS: { label: "In progress", cls: "chip-warn" },
  TODO: { label: "Not started", cls: "chip-neutral" },
  BLOCKED: { label: "Blocked", cls: "chip-bad" },
  SKIPPED: { label: "Skipped", cls: "chip-neutral" },
};

function normType(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function docStatusCopy(status: string) {
  return DOC_STATUS_COPY[status] ?? { label: status, cls: "chip-neutral" };
}

function severityChip(severity: string): string {
  if (severity === "CRITICAL" || severity === "HIGH") return "chip-bad";
  if (severity === "MEDIUM") return "chip-warn";
  return "chip-neutral";
}

function DocRow({
  label,
  doc,
  fallbackType,
  today,
  busy,
  onSetStatus,
  onAdd,
}: {
  label: string;
  doc: DocumentItem | null;
  fallbackType: string;
  today: string | null;
  busy: boolean;
  onSetStatus: (id: string, status: string) => void;
  onAdd: (documentType: string) => void;
}) {
  const status = doc?.status ?? "TODO";
  const copy = docStatusCopy(status);
  const expired = !!(doc?.expires_at && today && doc.expires_at.slice(0, 10) < today);
  return (
    <li className="flex flex-wrap items-center gap-3 py-2 first:pt-0 last:pb-0">
      {doc ? (
        <input
          type="checkbox"
          id={`doc-${fallbackType}`}
          checked={status === "DONE"}
          disabled={busy}
          onChange={() => onSetStatus(doc.id, status === "DONE" ? "TODO" : "DONE")}
          aria-label={`Mark ${label} as ready`}
          className="h-4 w-4 shrink-0 accent-[#1D5C46] disabled:opacity-40"
        />
      ) : (
        <span aria-hidden className="h-4 w-4 shrink-0 rounded border border-line-dark" />
      )}
      {doc ? (
        <label
          htmlFor={`doc-${fallbackType}`}
          className="min-w-0 flex-1 cursor-pointer text-sm text-ink"
        >
          {label}
        </label>
      ) : (
        <span className="min-w-0 flex-1 text-sm text-ink-faint">{label}</span>
      )}
      {doc ? (
        <span className={`chip ${copy.cls}`}>{copy.label}</span>
      ) : (
        <button
          type="button"
          onClick={() => onAdd(fallbackType)}
          disabled={busy}
          className="btn-ghost btn-sm"
          aria-label={`Add ${label}`}
        >
          + Add
        </button>
      )}
      {doc?.expires_at && (
        <span className={`text-xs tabular-nums ${expired ? "text-danger" : "text-ink-faint"}`}>
          {expired ? "Expired " : "Expires "}
          {fmtDate(doc.expires_at)}
        </span>
      )}
    </li>
  );
}

function EvidenceDrawer({ programId, onClose }: { programId: string; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
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
      {isError && (
        <p className="text-sm text-ink-faint">
          Evidence is unavailable right now — the backend may be restarting.
        </p>
      )}
      {!isLoading && !isError && (data?.items ?? []).length === 0 ? (
        <p className="text-sm text-ink-faint">
          No verified evidence yet for this program — claims appear after a research run
          extracts them.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {(data?.items ?? []).map((e: EvidenceItem) => (
            <li key={e.id} className="py-2.5 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <ConfidencePill confidence={e.confidence} />
                <EvidenceStatusChip status={e.status} />
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

function MoveRow({ move }: { move: PortfolioMove }) {
  return (
    <li className="flex items-baseline gap-2 text-sm">
      <span className="display shrink-0 text-sm font-medium tabular-nums text-forest">
        {move.priority}
      </span>
      <Link href={`/programs/${move.program_id}`} className="link text-ink">
        {move.program_name ?? "Program"}
      </Link>
      <span className="chip chip-neutral ml-auto shrink-0">
        {move.category.replace("_", " ")}
      </span>
    </li>
  );
}

export default function DashboardPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [sim, setSim] = useState<SimulationResult | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [today, setToday] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });
  const detail = useQuery({
    queryKey: ["strategy", selected],
    queryFn: () => getStrategy(selected!),
    enabled: !!selected,
  });
  const evidenceHealth = useQuery({
    queryKey: ["evidence-health", selected],
    queryFn: () => getEvidenceHealth(selected!),
    enabled: !!selected,
  });
  const documents = useQuery({ queryKey: ["documents"], queryFn: listDocuments });
  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });

  // Client-only "today" so expiry checks never mismatch server markup.
  useEffect(() => {
    setToday(new Date().toISOString().slice(0, 10));
  }, []);

  const updateDoc = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => updateDocument(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
  const addDoc = useMutation({
    mutationFn: (documentType: string) => createDocument(documentType),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });

  const simulate = useMutation({
    mutationFn: () => simulateStrategy(selected!, scenario),
    onSuccess: (out) => {
      setSim(out);
      queryClient.invalidateQueries({ queryKey: ["strategy", selected] });
    },
    onError: (err) =>
      setSim({
        scenario,
        error: err instanceof Error ? err.message : "The simulation could not be run.",
      }),
  });

  const items = useMemo(() => strategies.data?.items ?? [], [strategies.data]);

  // Auto-select the newest strategy so the dashboard is never a blank slate.
  useEffect(() => {
    if (!selected && items.length > 0) setSelected(items[0].id);
  }, [items, selected]);

  const active = items.find((s) => s.id === selected);

  const missingFields = completion.data?.missing_fields ?? [];
  const profileIncomplete =
    (completion.data?.profile_completion ?? 100) < 100 && missingFields.length > 0;

  async function exportPdf() {
    if (!selected) return;
    setExporting(true);
    setExportError(null);
    try {
      const blob = await exportStrategyPdf(selected);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `admitgraph-strategy-${selected}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      const raw = e instanceof Error ? e.message : "";
      // "Export failed (500)" already says it — keep the state, skip the echo.
      setExportError(raw.includes("Export failed") ? "" : raw);
    } finally {
      setExporting(false);
    }
  }

  /* ------------------------------------------------ Application readiness */
  const readinessRows = useMemo(() => {
    const docItems = documents.data?.items ?? [];
    const remaining = [...docItems];
    const rows = DOC_TYPES.map((def) => {
      const idx = remaining.findIndex((d) =>
        def.aliases.some((alias) => normType(alias) === normType(d.document_type))
      );
      const doc = idx >= 0 ? remaining.splice(idx, 1)[0] : null;
      return { key: def.key, label: def.label, doc };
    });
    // Any other document type stored on the profile still gets a row.
    for (const extra of remaining) {
      rows.push({
        key: extra.document_type,
        label: extra.document_type.replaceAll("_", " "),
        doc: extra,
      });
    }
    return rows;
  }, [documents.data]);
  const readyCount = readinessRows.filter((r) => r.doc?.status === "DONE").length;

  /* ------------------------------------------------------------- simulator */
  const hasPortfolioDelta =
    !!sim &&
    !sim.error &&
    ((sim.portfolio_before?.length ?? 0) > 0 ||
      (sim.portfolio_after?.length ?? 0) > 0 ||
      !!sim.delta);
  const deltaGroups =
    sim?.delta &&
    [
      { label: "Moved up", glyph: "↑", cls: "chip-good", items: sim.delta.moved_up ?? [] },
      { label: "Moved down", glyph: "↓", cls: "chip-warn", items: sim.delta.moved_down ?? [] },
      { label: "Added", glyph: "+", cls: "chip-good", items: sim.delta.added ?? [] },
      { label: "Removed", glyph: "−", cls: "chip-bad", items: sim.delta.removed ?? [] },
    ].filter((g) => g.items.length > 0);

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Strategy"
        title="My Plan"
        lede="Your portfolio, risks and roadmap — each one traceable to the evidence it was built from."
        actions={
          <>
            <button
              type="button"
              onClick={exportPdf}
              disabled={!selected || exporting}
              className="btn-secondary"
            >
              {exporting ? "Exporting…" : "Export PDF"}
            </button>
            <Link href="/research" className="btn-primary">
              New research run
            </Link>
          </>
        }
      />

      {exportError !== null && (
        <div className="flex flex-wrap items-center gap-3">
          <ErrorNote message={`Export failed — try again.${exportError ? ` ${exportError}` : ""}`} />
          <button type="button" onClick={exportPdf} className="btn-secondary btn-sm">
            Retry export
          </button>
        </div>
      )}

      {strategies.isLoading && <LoadingNote what="Loading strategies…" />}
      {strategies.isError && (
        <ErrorNote
          message={`Could not load strategies: ${(strategies.error as Error).message}. The backend may be restarting — try again in a moment.`}
        />
      )}

      {strategies.isSuccess && items.length === 0 && (
        <div className="space-y-3">
          <EmptyState
            title="No strategies yet"
            body="Run the full example once and your portfolio, risks and roadmap appear here."
            action={
              <Link href="/research" className="btn-primary btn-sm">
                Run the full example
              </Link>
            }
          />
          {profileIncomplete && (
            <p className="text-sm text-ink-soft">
              Some profile answers are still missing ({missingFields.slice(0, 3).join(", ")}).
              Finish the{" "}
              <Link href="/onboarding" className="link">
                guided setup →
              </Link>{" "}
              and research will score against a complete picture.
            </p>
          )}
        </div>
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

      {/* Application readiness */}
      {strategies.isSuccess && (
        <Section
          index="—"
          title="Application readiness"
          aside={
            <span className="text-xs text-ink-faint">
              {readyCount} of {DOC_TYPES.length} ready
            </span>
          }
        >
          {documents.isLoading && <LoadingNote what="Loading your document checklist…" />}
          {documents.isError && (
            <ErrorNote
              message={`Could not load your checklist: ${(documents.error as Error).message}. Nothing was changed — try again shortly.`}
            />
          )}
          {documents.isSuccess && (
            <ul className="flex flex-col divide-y divide-line">
              {readinessRows.map((row) => (
                <DocRow
                  key={row.key}
                  label={row.label}
                  doc={row.doc}
                  fallbackType={row.key}
                  today={today}
                  busy={updateDoc.isPending || addDoc.isPending}
                  onSetStatus={(id, status) => updateDoc.mutate({ id, status })}
                  onAdd={(documentType) => addDoc.mutate(documentType)}
                />
              ))}
            </ul>
          )}
          {updateDoc.isError && (
            <div className="mt-3">
              <ErrorNote
                message={`Could not update that document: ${(updateDoc.error as Error).message}`}
              />
            </div>
          )}
          {addDoc.isError && (
            <div className="mt-3">
              <ErrorNote
                message={`Could not add that document: ${(addDoc.error as Error).message}`}
              />
            </div>
          )}
          <p className="mt-3 text-xs text-ink-faint">
            Track only — we never upload documents for you, and nothing here is sent to a
            university.
          </p>
        </Section>
      )}

      {/* Evidence health (MASTER_SPEC §17) */}
      {selected && (
        <Section index="—" title="Evidence health">
          {evidenceHealth.isLoading && <LoadingNote what="Checking evidence health…" />}
          {evidenceHealth.isError && (
            <ErrorNote message="Evidence health is unavailable right now — the backend may be restarting. Everything else on this page still works." />
          )}
          {evidenceHealth.data && (
            <div className="space-y-3">
              <p className="text-sm text-ink">
                <span className="display text-2xl font-medium tabular-nums text-forest">
                  {evidenceHealth.data.programs_with_evidence}/
                  {evidenceHealth.data.programs_total}
                </span>{" "}
                portfolio programs have at least one sourced claim (
                {evidenceHealth.data.evidence_total} claims total).
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs uppercase tracking-wide text-ink-faint">By status</span>
                {Object.keys(evidenceHealth.data.by_status).length === 0 && (
                  <span className="text-xs text-ink-faint">No claims extracted yet.</span>
                )}
                {Object.entries(evidenceHealth.data.by_status).map(([status, count]) => (
                  <span key={status} className="flex items-center gap-1.5">
                    <EvidenceStatusChip status={status} />
                    <span className="text-xs tabular-nums text-ink-faint">{count}</span>
                  </span>
                ))}
                <span
                  className={`chip ${
                    evidenceHealth.data.stale_count > 0 ? "chip-warn" : "chip-good"
                  }`}
                >
                  {evidenceHealth.data.stale_count} stale
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs uppercase tracking-wide text-ink-faint">
                  By confidence
                </span>
                {Object.keys(evidenceHealth.data.by_confidence).length === 0 && (
                  <span className="text-xs text-ink-faint">—</span>
                )}
                {Object.entries(evidenceHealth.data.by_confidence).map(([level, count]) => (
                  <span key={level} className="chip chip-neutral">
                    {level}: {count}
                  </span>
                ))}
              </div>
              {evidenceHealth.data.stale_count > 0 && (
                <p className="text-xs text-ink-soft">
                  Stale claims passed their freshness window — they stay visible, flagged, and
                  are re-verified on the next research run.
                </p>
              )}
            </div>
          )}
        </Section>
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
          <Section index="—" title="What could break this plan?">
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

            {sim?.error && (
              <div className="mt-3">
                <ErrorNote message={`Simulation failed: ${sim.error}`} />
              </div>
            )}

            {hasPortfolioDelta && sim && (
              <div className="mt-4 space-y-3">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="card p-3">
                    <p className="eyebrow mb-2">Before</p>
                    <ul className="flex flex-col gap-2">
                      {(sim.portfolio_before ?? []).map((m) => (
                        <MoveRow key={`b-${m.program_id}`} move={m} />
                      ))}
                      {(sim.portfolio_before ?? []).length === 0 && (
                        <li className="text-sm text-ink-faint">Not available.</li>
                      )}
                    </ul>
                  </div>
                  <div className="card p-3">
                    <p className="eyebrow mb-2">After</p>
                    <ul className="flex flex-col gap-2">
                      {(sim.portfolio_after ?? []).map((m) => (
                        <MoveRow key={`a-${m.program_id}`} move={m} />
                      ))}
                      {(sim.portfolio_after ?? []).length === 0 && (
                        <li className="text-sm text-ink-faint">Not available.</li>
                      )}
                    </ul>
                  </div>
                </div>

                {deltaGroups && deltaGroups.length > 0 && (
                  <div className="rounded-lg border border-line bg-paper/60 p-3">
                    <p className="eyebrow mb-2">What moved</p>
                    <ul className="flex flex-col gap-1.5 text-sm">
                      {deltaGroups.map((g) => (
                        <li key={g.label} className="flex flex-wrap items-baseline gap-2">
                          <span className={`chip ${g.cls}`}>
                            {g.glyph} {g.items.length}
                          </span>
                          <span className="font-medium text-ink">{g.label}</span>
                          <span className="text-xs text-ink-soft">
                            {g.items.map((m) => m.program_name ?? "Program").join(", ")}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {sim.delta?.summary && (
                      <p className="mt-2 border-t border-line pt-2 text-sm text-ink">
                        {sim.delta.summary}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {!hasPortfolioDelta && sim && !sim.error && sim.modified_profile && (
              <pre className="mt-3 overflow-x-auto rounded-md bg-paper-dark p-3 text-xs text-ink-soft">
                {`Scenario ${sim.scenario} applied to your profile:\n${JSON.stringify(
                  sim.modified_profile,
                  null,
                  2
                )}`}
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
