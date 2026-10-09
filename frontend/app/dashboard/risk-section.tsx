"use client";

import type { Dispatch, SetStateAction } from "react";
import { useMemo } from "react";
import { RiskActions } from "../components/risk-actions";
import { Section, SeverityChip } from "../components/ui";
import type { StrategyQuery } from "./sections-shared";


/** Risks preview before "Show all" — critical/high first (see riskRank). */
const RISK_PREVIEW_COUNT = 3;
const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };


export function RiskSection({
  detail,
  showAllRisks,
  setShowAllRisks,
}: {
  detail: StrategyQuery;
  showAllRisks: boolean;
  setShowAllRisks: Dispatch<SetStateAction<boolean>>;
}) {

  // Risks sorted critical-first (stable within severity) for the collapsed
  // preview; every row keeps its full RiskActions set when visible.
  const sortedRisks = useMemo(() => {
    const list = detail.data?.risks ?? [];
    return list
      .map((risk, index) => ({ risk, index }))
      .sort(
        (a, b) =>
          (SEVERITY_RANK[a.risk.severity.toLowerCase()] ?? 4) -
            (SEVERITY_RANK[b.risk.severity.toLowerCase()] ?? 4) || a.index - b.index
      )
      .map((entry) => entry.risk);
  }, [detail.data]);
  const visibleRisks = showAllRisks ? sortedRisks : sortedRisks.slice(0, RISK_PREVIEW_COUNT);


  if (!detail.data) return null;

  return (
    <>

          {/* Risks — collapsed preview: the 3 most severe first, everything
              else behind "Show all". Every visible row keeps its actions. */}
          <div id="risks" className="scroll-mt-8">
            <Section
              index="—"
              title="Risks"
              aside={
                sortedRisks.length > RISK_PREVIEW_COUNT ? (
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    aria-expanded={showAllRisks}
                    aria-controls="risk-list"
                    onClick={() => setShowAllRisks((v) => !v)}
                  >
                    {showAllRisks ? "Show fewer" : `Show all (${sortedRisks.length})`}
                  </button>
                ) : null
              }
            >
              {sortedRisks.length === 0 ? (
                <p className="text-sm text-ink-faint">No risks flagged.</p>
              ) : (
                <ul id="risk-list" className="flex flex-col gap-3">
                  {visibleRisks.map((r) => (
                    <li key={r.id} className="border-l-4 border-line-dark pl-3 text-sm">
                      <SeverityChip severity={r.severity} showRaw className="mr-2" />
                      <span className="font-medium text-ink">{r.title}</span>
                      <p className="mt-1 text-ink-soft">{r.reason}</p>
                      <p className="mt-0.5 text-xs text-ink-faint">→ {r.recommended_action}</p>
                      <div className="mt-1.5">
                        <RiskActions
                          strategyId={detail.data.id}
                          riskId={r.id}
                          status={r.status}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {!showAllRisks && sortedRisks.length > RISK_PREVIEW_COUNT && (
                <p className="mt-3 text-xs text-ink-faint">
                  Showing the {visibleRisks.length} most important of {sortedRisks.length} risks
                  — the rest are one click away.
                </p>
              )}
            </Section>
          </div>

    </>
  );
}
