"use client";

import type { Dispatch, SetStateAction } from "react";
import { Section } from "../components/ui";
import type { RoadmapTaskFull } from "../lib/api-extra";
import { EvidenceDrawer } from "./evidence-drawer";
import type { EvidenceView, StrategyQuery } from "./sections-shared";

export function RoadmapSection({
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

          {/* Roadmap — every task with evidence is traceable via "Why?" (§10) */}
          <div id="next-tasks" className="scroll-mt-8">
            <Section index="—" title="Next tasks">
              <ul className="flex flex-col divide-y divide-line text-sm">
                {detail.data.roadmap_tasks.map((raw) => {
                  const t = raw as RoadmapTaskFull;
                  const evIds = Array.isArray(t.evidence_ids) ? t.evidence_ids : [];
                  const whyOpen =
                    !!evidenceView &&
                    "evidenceIds" in evidenceView &&
                    evidenceView.evidenceIds.join("|") === evIds.join("|");
                  return (
                    <li key={t.id} className="flex items-baseline gap-3 py-2 first:pt-0 last:pb-0">
                      <span aria-hidden className="text-forest">
                        ○
                      </span>
                      <span className="flex-1 text-ink">
                        {t.title}
                        {evIds.length > 0 && (
                          <button
                            type="button"
                            className="link ml-2 text-xs text-ink-faint"
                            aria-expanded={whyOpen}
                            aria-label={`Why is “${t.title}” recommended?`}
                            onClick={() => setEvidenceView(whyOpen ? null : { evidenceIds: evIds })}
                          >
                            Why? ({evIds.length} source{evIds.length > 1 ? "s" : ""})
                          </button>
                        )}
                      </span>
                      {t.due_date && (
                        <span className="text-xs tabular-nums text-ink-faint">due {t.due_date}</span>
                      )}
                    </li>
                  );
                })}
                {detail.data.roadmap_tasks.length === 0 && (
                  <li className="py-2 text-ink-faint">No tasks generated yet.</li>
                )}
              </ul>
            </Section>
            {evidenceView && "evidenceIds" in evidenceView && (
              <EvidenceDrawer view={evidenceView} onClose={() => setEvidenceView(null)} />
            )}
          </div>

    </>
  );
}
