"use client";

import type { Dispatch, SetStateAction } from "react";
import Link from "next/link";
import { EvidenceStatusChip, SeverityChip, fmtDate } from "../components/ui";
import { EvidenceDrawer } from "./evidence-drawer";
import {
  CATEGORY_COPY,
  CATEGORY_HEADING,
  CATEGORY_ORDER,
  type EvidenceView,
  type StrategyQuery,
} from "./sections-shared";


/** Estimated cost band copy — backend sends {currency, amount, band} (band is
 *  LOW | MEDIUM | HIGH) or an empty object when nothing was estimated. */
function costCopy(cost: Record<string, unknown> | null | undefined): string {
  if (!cost || Object.keys(cost).length === 0) return "Not estimated yet";
  const band = typeof cost.band === "string" ? cost.band.toLowerCase() : null;
  const bandCopy = band === "low" ? "low" : band === "medium" ? "medium" : band === "high" ? "high" : null;
  const amount =
    typeof cost.amount === "number" && Number.isFinite(cost.amount)
      ? `${cost.amount}${typeof cost.currency === "string" ? ` ${cost.currency}` : ""}`
      : null;
  if (bandCopy && amount) return `${amount} (${bandCopy} band)`;
  if (amount) return amount;
  if (bandCopy) return `${bandCopy} band`;
  return "Not estimated yet";
}

/** Evidence freshness chip status: backend sends FRESH | STALE | UNKNOWN. */
function freshnessStatus(
  freshness: { status: string; stale: number; total: number } | null | undefined
): string {
  if (!freshness || !freshness.status) return "UNKNOWN";
  return freshness.status;
}

function freshnessCopy(
  freshness: { status: string; stale: number; total: number } | null | undefined
): string {
  if (!freshness || freshness.total === 0) return "no claims checked yet";
  if (freshness.stale === 0) return `${freshness.total} claims checked`;
  return `${freshness.stale} of ${freshness.total} stale`;
}


export function PortfolioSection({
  detail,
  evidenceView,
  setEvidenceView,
}: {
  detail: StrategyQuery;
  evidenceView: EvidenceView | null;
  setEvidenceView: Dispatch<SetStateAction<EvidenceView | null>>;
}) {
  if (!detail.data) return null;

  return (
    <>

          {/* Portfolio */}
          <section aria-label="Portfolio">
            <h2 className="display mb-3 text-lg font-medium">Portfolio</h2>
            <div className="grid gap-4 md:grid-cols-3">
              {CATEGORY_ORDER.map((cat) => (
                <div key={cat} className="card p-4">
                  <div className="mb-3 border-b border-line pb-2">
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-ink">
                      {CATEGORY_HEADING[cat] ?? cat.replace("_", " ")}
                    </h3>
                    <p className="text-xs text-ink-faint">{CATEGORY_COPY[cat]}</p>
                  </div>
                  <ul className="flex flex-col gap-4">
                    {detail.data.portfolio
                      .filter((p) => p.category === cat)
                      .map((p) => (
                        <li key={p.program_id} className="border-b border-line pb-3 last:border-0 last:pb-0">
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
                            {p.fit_score && (
                              <span
                                className="chip chip-neutral ml-auto shrink-0"
                                title="Fit score"
                              >
                                {p.fit_score}
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-ink-soft">{p.institution}</div>
                          <div className="mt-1 text-xs leading-relaxed text-ink-faint">
                            {p.rationale}
                          </div>

                          {/* top 2 reasons */}
                          {p.reasons.length > 0 && (
                            <ul className="mt-1.5 flex flex-col gap-0.5 text-xs text-ink-soft">
                              {p.reasons.slice(0, 2).map((reason) => (
                                <li key={reason} className="flex gap-1.5">
                                  <span aria-hidden className="text-forest">
                                    ✓
                                  </span>
                                  <span>{reason}</span>
                                </li>
                              ))}
                            </ul>
                          )}

                          {/* top risk */}
                          {p.top_risk && (
                            <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                              <SeverityChip severity={p.top_risk.severity} />
                              <span className="text-ink-soft">{p.top_risk.title}</span>
                            </p>
                          )}

                          {/* next deadline + estimated cost band */}
                          <dl className="mt-1.5 grid grid-cols-1 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-2">
                            <div className="flex gap-1.5">
                              <dt className="text-ink-faint">Next deadline</dt>
                              <dd className="text-ink-soft">
                                {p.next_deadline ? fmtDate(p.next_deadline) : "—"}
                              </dd>
                            </div>
                            <div className="flex gap-1.5">
                              <dt className="text-ink-faint">Est. cost</dt>
                              <dd className="text-ink-soft">{costCopy(p.estimated_cost)}</dd>
                            </div>
                          </dl>

                          {/* evidence freshness */}
                          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                            <span className="text-ink-faint">Evidence</span>
                            <EvidenceStatusChip status={freshnessStatus(p.evidence_freshness)} />
                            <span className="text-ink-faint">
                              {freshnessCopy(p.evidence_freshness)}
                            </span>
                          </p>

                          <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs">
                            <button
                              className="link text-ink-soft"
                              onClick={() =>
                                setEvidenceView(
                                  evidenceView &&
                                    "programId" in evidenceView &&
                                    evidenceView.programId === p.program_id
                                    ? null
                                    : { programId: p.program_id }
                                )
                              }
                              aria-expanded={
                                !!(
                                  evidenceView &&
                                  "programId" in evidenceView &&
                                  evidenceView.programId === p.program_id
                                )
                              }
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

          {evidenceView && "programId" in evidenceView && (
            <EvidenceDrawer view={evidenceView} onClose={() => setEvidenceView(null)} />
          )}

    </>
  );
}
