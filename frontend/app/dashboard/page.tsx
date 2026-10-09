"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ErrorNote, fmtDate } from "../components/ui";
import { getStrategies, getStrategy, listPrograms } from "../lib/api";
import { DocumentsSection } from "./documents-section";
import { EvidenceHealthSection } from "./evidence-health-section";
import { GettingStartedSection } from "./getting-started-section";
import { PlanHeader } from "./plan-header";
import { PortfolioSection } from "./portfolio-section";
import { RiskSection } from "./risk-section";
import { RoadmapSection } from "./roadmap-section";
import { SimulatorSection } from "./simulator-section";
import { dueCopy, type EvidenceView } from "./sections-shared";

export default function DashboardPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [evidenceView, setEvidenceView] = useState<EvidenceView | null>(null);
  const [today, setToday] = useState<string | null>(null);
  const [showAllRisks, setShowAllRisks] = useState(false);

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });
  const detail = useQuery({
    queryKey: ["strategy", selected],
    queryFn: () => getStrategy(selected!),
    enabled: !!selected,
  });
  // Country choices for "I remove a country" come from the programs we hold —
  // no hardcoded list, so the selector always matches real data.
  const programs = useQuery({
    queryKey: ["programs", "for-simulator"],
    queryFn: () => listPrograms(1, 100),
  });

  // Client-only "today" so expiry checks never mismatch server markup.
  useEffect(() => {
    setToday(new Date().toISOString().slice(0, 10));
  }, []);

  const items = useMemo(() => strategies.data?.items ?? [], [strategies.data]);

  // Auto-select the newest strategy so the dashboard is never a blank slate.
  useEffect(() => {
    if (!selected && items.length > 0) setSelected(items[0].id);
  }, [items, selected]);

  const active = items.find((s) => s.id === selected);

  // Urgent action: roadmap due dates and portfolio deadlines inside 30 days.
  // Pure client date math over data we already have — never invented urgency.
  const urgent = useMemo(() => {
    if (!detail.data || !today) return [];
    const out: { key: string; title: string; days: number; href?: string }[] = [];
    const todayMs = Date.parse(`${today}T00:00:00Z`);
    const daysUntil = (iso: string) =>
      Math.round((Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) - todayMs) / 86_400_000);

    for (const t of detail.data.roadmap_tasks) {
      if (!t.due_date || t.status === "DONE" || t.status === "CANCELLED") continue;
      const days = daysUntil(t.due_date);
      if (days <= 30) out.push({ key: `task-${t.id}`, title: t.title, days });
    }
    for (const p of detail.data.portfolio) {
      if (!p.next_deadline) continue;
      const days = daysUntil(p.next_deadline);
      if (days <= 30) {
        out.push({
          key: `deadline-${p.program_id}`,
          title: `${p.program_name ?? "Program"} — application deadline`,
          days,
          href: `/programs/${p.program_id}`,
        });
      }
    }
    return out.sort((a, b) => b.days - a.days).slice(0, 5);
  }, [detail.data, today]);

  // Collapse the risk list per strategy switch — the preview is per strategy.
  useEffect(() => {
    setShowAllRisks(false);
  }, [selected]);


  return (
    <main className="mx-auto max-w-5xl space-y-8 px-5 py-8">
      <PlanHeader selected={selected} strategies={strategies} />
      <GettingStartedSection
        strategies={strategies}
        items={items}
        urgent={urgent}
        detail={detail}
      />

      {/* Strategy selector */}
      {items.length > 0 && (
        <nav className="flex flex-wrap gap-2" aria-label="Strategies">
          {items.map((s) => (
            <button
              key={s.id}
              onClick={() => {
                setSelected(s.id);
                setEvidenceView(null);
              }}
              aria-pressed={selected === s.id}
              className={selected === s.id ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            >
              {fmtDate(s.created_at)} · health {s.plan_health_score ?? "–"}
            </button>
          ))}
        </nav>
      )}

      {/* getStrategy failed (network, 5xx, or a strict-parse ParseError):
          surface it instead of silently rendering the empty defaults —
          without this the page showed planReady=false and "No risks". */}
      {selected && detail.isError && (
        <div className="space-y-3">
          <ErrorNote
            message={`Could not load your plan: ${(detail.error as Error).message}. The backend may be restarting — try again in a moment.`}
          />
          <button
            type="button"
            onClick={() => void detail.refetch()}
            className="btn-secondary btn-sm"
          >
            Try again
          </button>
        </div>
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

      {/* Urgent action — FRONTEND_SPEC §Strategy dashboard "Top" block.
          Deadlines inside 30 days are computed client-side from the already
          loaded strategy payload (no extra request, no backend date logic). */}
      {detail.data && urgent.length > 0 && (
        <section
          className="rounded-lg border border-danger bg-danger-tint px-4 py-3"
          aria-label="Urgent action"
        >
          <p className="eyebrow mb-1 text-danger">Urgent action</p>
          <ul className="flex flex-col gap-1 text-sm text-ink">
            {urgent.map((u) => (
              <li key={u.key} className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium">{u.title}</span>
                <span className="text-xs text-ink-soft">{dueCopy(u.days)}</span>
                {u.href && (
                  <Link href={u.href} className="link text-xs">
                    Open →
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}


      {detail.data && (
        <>
          <PortfolioSection
            detail={detail}
            evidenceView={evidenceView}
            setEvidenceView={setEvidenceView}
          />
          <RiskSection
            detail={detail}
            showAllRisks={showAllRisks}
            setShowAllRisks={setShowAllRisks}
          />
          <SimulatorSection selected={selected} programs={programs} />
          <RoadmapSection
            detail={detail}
            evidenceView={evidenceView}
            setEvidenceView={setEvidenceView}
          />
        </>
      )}

      <DocumentsSection today={today} strategies={strategies} />
      <EvidenceHealthSection
        selected={selected}
        evidenceView={evidenceView}
        setEvidenceView={setEvidenceView}
      />
    </main>
  );
}
