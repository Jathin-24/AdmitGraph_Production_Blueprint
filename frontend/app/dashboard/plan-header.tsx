"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthNudge } from "../components/auth-nudge";
import { ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import { exportStrategyPdf } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { StrategiesQuery } from "./sections-shared";

export function PlanHeader({
  selected,
  strategies,
}: {
  selected: string | null;
  strategies: StrategiesQuery;
}) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const { status: authStatus } = useAuth();

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


  return (
    <>

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
        <div className="space-y-3">
          <ErrorNote
            message={`Could not load strategies: ${(strategies.error as Error).message}. The backend may be restarting — try again in a moment.`}
          />
          <button
            type="button"
            onClick={() => void strategies.refetch()}
            className="btn-secondary btn-sm"
          >
            Try again
          </button>
        </div>
      )}

      {/* Guest mode — the backend serves demo data to anonymous visitors, so
          the content stays visible but is clearly labelled as a demo. */}
      {authStatus === "anonymous" && (
        <div className="flex flex-wrap items-center gap-2" role="note">
          <span className="chip chip-warn">Demo plan</span>
          <span className="text-sm text-ink-soft">
            You&apos;re viewing a demo plan — create your own account to build yours.
          </span>
        </div>
      )}
      <AuthNudge next="/dashboard" />

    </>
  );
}
