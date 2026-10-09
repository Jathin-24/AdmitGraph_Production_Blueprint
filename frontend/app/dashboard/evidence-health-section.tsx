"use client";

import { useQuery } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";
import { Disclosure, ErrorNote, EvidenceStatusChip, LoadingNote } from "../components/ui";
import { getEvidenceHealthFull } from "../lib/api-extra";
import { EvidenceDrawer } from "./evidence-drawer";
import type { EvidenceView } from "./sections-shared";


/** MASTER_SPEC §17 evidence health — friendly labels for the backend's
 *  SourceAuthority enum keys (unknown keys fall back to a readable form). */
const AUTHORITY_COPY: Record<string, string> = {
  OFFICIAL_UNIVERSITY: "Official university",
  OFFICIAL_GOVERNMENT: "Official government",
  OFFICIAL_ORGANIZATION: "Official organization",
  ACCREDITED_BODY: "Accredited body",
  CREDIBLE_SECONDARY: "Credible secondary",
  NEWS: "News",
  FORUM_SOCIAL: "Forum / social",
  UNKNOWN: "Unknown",
};

function authorityLabel(key: string): string {
  return AUTHORITY_COPY[key] ?? key.replaceAll("_", " ");
}


export function EvidenceHealthSection({
  selected,
  evidenceView,
  setEvidenceView,
}: {
  selected: string | null;
  evidenceView: EvidenceView | null;
  setEvidenceView: Dispatch<SetStateAction<EvidenceView | null>>;
}) {
  const evidenceHealth = useQuery({
    queryKey: ["evidence-health", selected],
    queryFn: () => getEvidenceHealthFull(selected!),
    enabled: !!selected,
  });

  return (
    <>

      {/* Evidence health (MASTER_SPEC §17) — reference material: how much of
          the plan is actually backed by a sourced claim. */}
      {selected && (
        <Disclosure summary="Evidence health" hint="How well-sourced your plan is">
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

              {/* MASTER_SPEC §17 — source authority distribution. */}
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs uppercase tracking-wide text-ink-faint">
                  By source authority
                </span>
                {Object.keys(evidenceHealth.data.by_authority ?? {}).length === 0 && (
                  <span className="text-xs text-ink-faint">—</span>
                )}
                {Object.entries(evidenceHealth.data.by_authority ?? {}).map(([auth, count]) => (
                  <span key={auth} className="chip chip-neutral" title={`Source authority: ${auth}`}>
                    {authorityLabel(auth)}: {count}
                  </span>
                ))}
              </div>

              {/* Unknowns + conflicts (§17: never guess — say what's unknown). */}
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className="chip chip-neutral cursor-help"
                  title="Facts we could not verify — shown as UNKNOWN, never guessed"
                >
                  {evidenceHealth.data.unknowns ?? 0} unknown
                </span>
                <span
                  className={`chip ${
                    (evidenceHealth.data.conflicting_count ?? 0) > 0 ? "chip-bad" : "chip-good"
                  }`}
                >
                  {evidenceHealth.data.conflicting_count ?? 0} conflicting
                </span>
                {(evidenceHealth.data.conflicting_count ?? 0) > 0 && (
                  <button
                    type="button"
                    className="link text-xs"
                    aria-expanded={
                      !!(
                        evidenceView &&
                        "status" in evidenceView &&
                        evidenceView.status === "CONFLICTING"
                      )
                    }
                    onClick={() =>
                      setEvidenceView(
                        evidenceView &&
                          "status" in evidenceView &&
                          evidenceView.status === "CONFLICTING"
                          ? null
                          : { status: "CONFLICTING" }
                      )
                    }
                  >
                    Review conflicts →
                  </button>
                )}
              </div>
              {(evidenceHealth.data.unknowns ?? 0) > 0 && (
                <p className="text-xs text-ink-soft">
                  Unknowns are facts we could not verify from a live source — they show as
                  UNKNOWN, never guessed.
                </p>
              )}
              {evidenceHealth.data.stale_count > 0 && (
                <p className="text-xs text-ink-soft">
                  Stale claims passed their freshness window — they stay visible, flagged, and
                  are re-verified on the next research run.
                </p>
              )}
              {evidenceView && "status" in evidenceView && (
                <EvidenceDrawer view={evidenceView} onClose={() => setEvidenceView(null)} />
              )}
            </div>
          )}
        </Disclosure>
      )}

    </>
  );
}
