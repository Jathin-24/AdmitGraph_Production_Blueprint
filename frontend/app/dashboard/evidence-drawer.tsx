"use client";

import { useQuery } from "@tanstack/react-query";
import { RecheckButton } from "../components/evidence-actions";
import { ConfidencePill, EvidenceStatusChip, LoadingNote, fmtDate } from "../components/ui";
import { listEvidence, type EvidenceItem } from "../lib/api";
import { sanitizeHttpUrl } from "../lib/api-extra";
import type { EvidenceView } from "./sections-shared";


export function EvidenceDrawer({ view, onClose }: { view: EvidenceView; onClose: () => void }) {
  const programId = "programId" in view ? view.programId : undefined;
  const evidenceIds = "evidenceIds" in view ? view.evidenceIds : undefined;
  const statusFilter = "status" in view ? view.status : undefined;
  // Program views fetch only that program; id/status views fetch the shared
  // store once (one cache entry for both modes) and filter client-side.
  const { data, isLoading, isError } = useQuery({
    queryKey: programId ? ["evidence", programId] : ["evidence", "all"],
    queryFn: () => listEvidence(programId),
  });

  const all = data?.items ?? [];
  const items = programId
    ? all
    : evidenceIds
      ? all.filter((e) => evidenceIds.includes(e.id))
      : all.filter((e) => e.status === statusFilter);
  const missingIds = evidenceIds
    ? evidenceIds.filter((id) => !all.some((e) => e.id === id))
    : [];

  const title = programId
    ? "Evidence"
    : evidenceIds
      ? "Why this task?"
      : "Conflicting evidence";

  return (
    <div className="card p-4" role="region" aria-label="Evidence">
      <div className="mb-3 flex items-center justify-between border-b border-line pb-2">
        <h3 className="display text-base font-medium">{title}</h3>
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
      {!isLoading && !isError && items.length === 0 ? (
        <p className="text-sm text-ink-faint">
          {programId
            ? "No verified evidence yet for this program — claims appear after a research run extracts them."
            : evidenceIds
              ? "These specific sources aren’t in the evidence store yet — they appear after the next research run verifies them."
              : "No conflicting evidence right now — every checked claim has one agreed answer."}
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {items.map((e: EvidenceItem) => {
            // Backend-provided URLs are hrefs only when they're http(s).
            const sourceHref = sanitizeHttpUrl(e.source_url);
            return (
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
                  {sourceHref ? (
                    <a href={sourceHref} target="_blank" rel="noreferrer" className="link">
                      Open source ↗
                    </a>
                  ) : (
                    e.source_url && <span>Source URL: {e.source_url}</span>
                  )}
                  <RecheckButton evidenceId={e.id} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {!isLoading && !isError && missingIds.length > 0 && (
        <p className="mt-2 text-xs text-ink-faint">
          {missingIds.length} of these sources aren’t loaded on this page yet — they show up
          after the next research run re-verifies them.
        </p>
      )}
    </div>
  );
}
