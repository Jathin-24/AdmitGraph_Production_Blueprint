"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import {
  ConfidencePill,
  EmptyState,
  ErrorNote,
  EvidenceStatusChip,
  LoadingNote,
  Section,
  SeverityChip,
  StatusPill,
  fmtDate,
  statusSpec,
} from "../../components/ui";
import { RecheckButton, ResolveConflictButton } from "../../components/evidence-actions";
import { RiskActions } from "../../components/risk-actions";
import {
  createSubscription,
  getEvidenceConflicts,
  getFit,
  getProgram,
  getProgramRequirements,
  getProfile,
  getStrategies,
  getStrategy,
  listEvidence,
  listPrograms,
  saveProgram,
  unsaveProgram,
  type EvidenceItem,
  type ProfileOut,
  type RequirementItem,
} from "../../lib/api";

const CATEGORIES: { label: string; types: string[]; keys: string[] }[] = [
  { label: "Academic background", types: ["academic"], keys: ["cgpa_min", "academic_cgpa_min", "backlogs_max"] },
  { label: "Prerequisites", types: ["prerequisite"], keys: ["prerequisite_subjects"] },
  { label: "Language", types: ["language"], keys: ["ielts_overall_min"] },
  { label: "Tests", types: ["test"], keys: [] },
  { label: "Documents", types: ["document"], keys: [] },
  { label: "Deadline", types: ["deadline"], keys: ["application_deadline"] },
  { label: "Financial", types: ["tuition", "budget"], keys: ["tuition_max", "budget_min"] },
  { label: "Country / policy", types: ["policy", "country"], keys: [] },
];

const SEVERITY_RANK: Record<string, number> = {
  NOT_SATISFIED: 0,
  CONFLICTING: 1,
  UNKNOWN: 2,
  NEEDS_VERIFICATION: 3,
  PARTIAL: 4,
  SATISFIED: 5,
  NOT_APPLICABLE: 6,
};

function worstStatus(reqs: RequirementItem[]): string {
  if (reqs.length === 0) return "UNKNOWN";
  return [...reqs].sort(
    (a, b) => (SEVERITY_RANK[a.status] ?? 9) - (SEVERITY_RANK[b.status] ?? 9)
  )[0].status;
}

function formatValue(value: Record<string, unknown>): string {
  if (value.date) return String(value.date);
  if (value.min !== undefined && value.scale !== undefined) {
    return `≥ ${String(value.min)} on a ${String(value.scale)} scale`;
  }
  if (value.min !== undefined) return `≥ ${String(value.min)}`;
  if (value.max !== undefined) return `≤ ${String(value.max)}`;
  if (value.amount !== undefined) {
    return `${String(value.amount)}${value.currency ? ` ${String(value.currency)}` : ""}`;
  }
  if (Array.isArray(value.subjects)) return (value.subjects as string[]).join(", ");
  return JSON.stringify(value);
}

function youColumn(category: string, p: ProfileOut | null): string {
  if (!p) return "—";
  if (category === "Academic background") {
    if (p.cgpa) return `CGPA ${p.cgpa}${p.cgpa_scale ? ` / ${p.cgpa_scale}` : ""}`;
    if (p.percentage) return `${p.percentage}%`;
    return "Not provided";
  }
  if (category === "Financial") {
    return p.total_budget_amount
      ? `${p.total_budget_amount} ${p.budget_currency ?? ""}`.trim()
      : "Not provided";
  }
  if (category === "Country / policy") return p.institution_country_code ?? "Not provided";
  return "Not provided";
}

function ConflictDetail({ evidenceId }: { evidenceId: string }) {
  const conflicts = useQuery({
    queryKey: ["evidence-conflicts", evidenceId],
    queryFn: () => getEvidenceConflicts(evidenceId),
  });
  if (conflicts.isLoading) return <LoadingNote what="Loading conflict details…" />;
  if (conflicts.isError) {
    return (
      <p className="mt-2 text-xs text-ink-faint">
        Conflict details are unavailable right now — the backend may be restarting.
      </p>
    );
  }
  const items = conflicts.data?.items ?? [];
  if (items.length === 0) {
    return (
      <p className="mt-2 text-xs text-ink-faint">
        No conflict record is stored for this claim yet — treat it as unverified until the next
        research run re-checks it.
      </p>
    );
  }
  return (
    <ul className="mt-2 flex flex-col gap-2 rounded-md border border-danger/30 bg-danger-tint p-2.5 text-xs">
      {items.map((c) => {
        const others = (c.evidence_ids ?? []).filter((id) => id !== evidenceId);
        return (
          <li key={c.id}>
            <span className="font-medium text-ink">
              {c.description ?? c.reason ?? "Two sources give different values for this claim."}
            </span>
            {c.resolution_status && (
              <span className="chip chip-neutral ml-2">{c.resolution_status}</span>
            )}
            {others.length > 0 && (
              <span className="mt-0.5 block tabular-nums text-ink-soft">
                Disagreeing evidence: {others.join(", ")}
              </span>
            )}
            {!c.resolution_status && !c.resolved_at && (
              <span className="mt-1 block">
                <ResolveConflictButton conflictId={c.id} />
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function EvidenceRow({ e }: { e: EvidenceItem }) {
  const [showConflicts, setShowConflicts] = useState(false);
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <ConfidencePill confidence={e.confidence} />
        <EvidenceStatusChip status={e.status} />
        <span className="text-[11px] uppercase tracking-wide text-ink-faint">
          {e.claim_type}
        </span>
      </div>
      <p className="mt-1.5 text-sm text-ink">{e.claim}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-faint">
        {e.source_domain && <span>Source: {e.source_domain}</span>}
        {e.source_authority && <span>Authority: {e.source_authority}</span>}
        <span>Retrieved: {fmtDate(e.retrieved_at)}</span>
        {e.freshness_deadline && <span>Fresh until: {fmtDate(e.freshness_deadline)}</span>}
        {e.source_url && (
          <a href={e.source_url} target="_blank" rel="noreferrer" className="link">
            Open source ↗
          </a>
        )}
        <RecheckButton evidenceId={e.id} />
      </div>
      {e.status === "CONFLICTING" && (
        <button
          type="button"
          className="link mt-1.5 text-xs text-danger"
          onClick={() => setShowConflicts((v) => !v)}
          aria-expanded={showConflicts}
        >
          {showConflicts ? "Hide conflict details" : "Show conflict details"}
        </button>
      )}
      {e.status === "CONFLICTING" && showConflicts && <ConflictDetail evidenceId={e.id} />}
    </li>
  );
}

function EvidenceList({ items }: { items: EvidenceItem[] }) {
  return (
    <ul className="flex flex-col divide-y divide-line">
      {items.map((e) => (
        <EvidenceRow key={e.id} e={e} />
      ))}
    </ul>
  );
}

export default function ProgramDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const queryClient = useQueryClient();
  const [evidenceFilter, setEvidenceFilter] = useState<string[] | null>(null);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => getProgram(id) });
  const requirements = useQuery({
    queryKey: ["program-requirements", id],
    queryFn: () => getProgramRequirements(id),
  });
  const evidence = useQuery({ queryKey: ["program-evidence", id], queryFn: () => listEvidence(id) });
  const profile = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });

  const latestId = strategies.data?.items?.[0]?.id;
  const detail = useQuery({
    queryKey: ["strategy", latestId],
    queryFn: () => getStrategy(latestId!),
    enabled: !!latestId,
  });
  const fit = useQuery({
    queryKey: ["fit", latestId],
    queryFn: () => getFit(latestId!),
    enabled: !!latestId,
  });

  const savedIds = useQuery({
    // Same queryKey AND same shape as /explore's saved query — react-query
    // shares this cache entry across pages, so both must agree.
    queryKey: ["saved-programs"],
    queryFn: () => listPrograms(1, 100, true),
    // Normalize to a Set, tolerating either shape in a warm cache.
    select: (raw: unknown): Set<string> =>
      raw instanceof Set
        ? (raw as Set<string>)
        : new Set(
            ((raw as { items?: { id: string }[] })?.items ?? []).map((sp) => sp.id)
          ),
  });
  const isSaved = savedIds.data?.has(id) ?? false;

  const save = useMutation({
    mutationFn: () => (isSaved ? unsaveProgram(id) : saveProgram(id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saved-programs"] });
      queryClient.invalidateQueries({ queryKey: ["programs"] });
    },
  });

  const watch = useMutation({
    mutationFn: () =>
      createSubscription({ field_key: "deadline", frequency: "WEEKLY", program_id: id }),
  });

  if (program.isLoading) {
    return (
      <main className="mx-auto max-w-5xl px-5 py-8">
        <LoadingNote what="Loading program…" />
      </main>
    );
  }
  if (program.isError) {
    return (
      <main className="mx-auto max-w-5xl space-y-4 px-5 py-8">
        <ErrorNote
          message={`Could not load this program: ${(program.error as Error).message}.`}
        />
        <Link href="/explore" className="link text-sm">
          ← Back to Explore
        </Link>
      </main>
    );
  }

  const p = program.data!;
  const reqs = requirements.data?.items ?? [];
  const ev = evidence.data?.items ?? [];
  const filteredEvidence = evidenceFilter ? ev.filter((e) => evidenceFilter.includes(e.id)) : [];
  const fitItem = fit.data?.items.find((f) => f.program_id === id);
  const satisfied = reqs.filter((r) => r.status === "SATISFIED").length;
  const blockers = reqs.filter(
    (r) => r.mandatory && (r.status === "NOT_SATISFIED" || r.status === "CONFLICTING")
  );
  const deadlineReq = reqs.find((r) => r.normalized_key === "application_deadline");
  const tuitionReq = reqs.find((r) => r.normalized_key === "tuition_max");
  const risks = detail.data?.risks ?? [];
  const tasks = detail.data?.roadmap_tasks ?? [];

  return (
    <main className="mx-auto max-w-5xl space-y-5 px-5 py-8">
      {/* Header */}
      <header className="border-b border-line pb-6">
        <Link href="/explore" className="eyebrow mb-3 block hover:text-forest-dark">
          ← Explore
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <p className="text-sm text-ink-soft">
              {p.institution ?? "Institution unknown"}
              {p.city ? ` · ${p.city}` : ""}
              {p.country_code ? ` · ${p.country_code}` : ""}
            </p>
            <h1 className="display mt-1 text-[2rem] font-medium leading-[1.1] text-ink">
              {p.name}
            </h1>
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => save.mutate()} disabled={save.isPending} className={isSaved ? "btn-secondary" : "btn-primary"}>
              {isSaved ? "Saved ✓" : "Save to my plan"}
            </button>
            <button onClick={() => watch.mutate()} disabled={watch.isPending} className="btn-secondary">
              {watch.isSuccess ? "Watching ✓" : "Watch deadline"}
            </button>
          </div>
        </div>
        {watch.isSuccess && (
          <p className="mt-3 text-sm text-forest" role="status">
            Weekly deadline monitoring started — see the{" "}
            <Link href="/monitor" className="link">
              Monitor page
            </Link>
            .
          </p>
        )}
      </header>

      {/* No plan yet — guide to the example run */}
      {strategies.isSuccess && (strategies.data?.items ?? []).length === 0 && (
        <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
          <p className="max-w-2xl text-sm text-ink-soft">
            You don&apos;t have a plan yet. Run the full example to build a portfolio and see how
            this program fits your profile.
          </p>
          <Link href="/research" className="btn-primary btn-sm">
            Run the full example
          </Link>
        </div>
      )}

      {/* 1. Overview */}
      <Section index="01" title="Overview">
        <div className="flex flex-wrap gap-1.5">
          {[p.degree_type, p.field_of_study, p.specialization, p.language, p.active ? "Active" : "Inactive"]
            .filter(Boolean)
            .map((b, i) => (
              <span key={`badge-${i}`} className="chip-neutral">
                {String(b)}
              </span>
            ))}
          {p.duration_months && <span className="chip-neutral">{p.duration_months} months</span>}
        </div>
        <div className="mt-3 flex flex-wrap gap-4 text-sm">
          {p.official_url && (
            <a href={p.official_url} target="_blank" rel="noreferrer" className="link">
              Official program page ↗
            </a>
          )}
          <span className="text-ink-faint">
            {p.requirement_count} requirements · {p.evidence_count} evidence claims
          </span>
          <span className="text-ink-faint">Last verified: {fmtDate(p.last_verified_at)}</span>
        </div>
      </Section>

      {/* 2. Why it fits */}
      <Section index="02" title="Why it fits">
        {fitItem ? (
          <div>
            <p className="display text-3xl font-medium text-forest">
              {fitItem.overall_score}
              <span className="text-lg text-ink-faint"> / 100 fit</span>
            </p>
            <p className="mt-2 text-sm text-ink-soft">
              {fitItem.explanation ??
                "Score computed from your stored profile and this program's evidence."}
            </p>
            <p className="mt-2 text-xs text-ink-faint">
              This is an explainable product score, not an admission probability.
            </p>
          </div>
        ) : (
          <p className="text-sm text-ink-soft">
            {reqs.length > 0
              ? `${satisfied} of ${reqs.length} known requirements look satisfied so far. Fit scoring appears after a research run completes for your profile.`
              : "Not enough evidence yet — fit scoring appears after a research run extracts this program's requirements."}
          </p>
        )}
      </Section>

      {/* 3. Eligibility matrix */}
      <Section index="03" title="Eligibility matrix">
        <div className="overflow-x-auto">
          <table className="table-editorial min-w-[720px]">
            <thead>
              <tr>
                <th>Area</th>
                <th>You</th>
                <th>Requirement</th>
                <th>Status</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {CATEGORIES.map((cat) => {
                const catReqs = reqs.filter(
                  (r) => cat.types.includes(r.requirement_type) || cat.keys.includes(r.normalized_key)
                );
                const spec = statusSpec(worstStatus(catReqs));
                const catEv = ev.filter(
                  (e) => e.normalized_claim && cat.keys.includes(e.normalized_claim)
                );
                const confidences = Array.from(new Set(catEv.map((e) => e.confidence)));
                const reqEvIds = Array.from(
                  new Set(catReqs.flatMap((r) => r.evidence_ids ?? []))
                );
                return (
                  <tr key={cat.label}>
                    <td className="font-medium text-ink">{cat.label}</td>
                    <td className="text-ink-soft">{youColumn(cat.label, profile.data ?? null)}</td>
                    <td className="text-ink-soft">
                      {catReqs.length > 0
                        ? catReqs.map((r) => formatValue(r.value)).join(" · ")
                        : "No requirement evidence yet"}
                    </td>
                    <td>
                      <span className={`chip ${spec.cls}`}>{spec.label}</span>
                    </td>
                    <td className="text-xs text-ink-faint">
                      {reqEvIds.length > 0 ? (
                        <button
                          type="button"
                          className="link"
                          onClick={() => setEvidenceFilter(reqEvIds)}
                          aria-expanded={evidenceFilter !== null}
                        >
                          {reqEvIds.length} source{reqEvIds.length > 1 ? "s" : ""}
                        </button>
                      ) : catEv.length > 0 ? (
                        `${catEv.length} claim${catEv.length > 1 ? "s" : ""} (${confidences.join(", ")})`
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {evidenceFilter && (
          <div className="card mt-3 p-4" role="region" aria-label="Sources for this area">
            <div className="mb-3 flex items-center justify-between border-b border-line pb-2">
              <h3 className="display text-base font-medium">
                {evidenceFilter.length} sourced claim{evidenceFilter.length > 1 ? "s" : ""}
              </h3>
              <button
                type="button"
                onClick={() => setEvidenceFilter(null)}
                aria-label="Close sources"
                className="btn-ghost btn-sm"
              >
                Close
              </button>
            </div>
            {filteredEvidence.length > 0 ? (
              <EvidenceList items={filteredEvidence} />
            ) : (
              <p className="text-sm text-ink-faint">
                These specific sources are not loaded on this page — see the Evidence section
                below for every claim we hold for this program.
              </p>
            )}
          </div>
        )}
      </Section>

      {/* 4. Risks */}
      <Section index="04" title="Risks">
        {blockers.length === 0 && risks.length === 0 ? (
          <p className="text-sm text-ink-faint">No open risks flagged for this program yet.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {blockers.map((r) => (
              <li key={r.id} className="rounded-md border-l-4 border-danger bg-danger-tint p-3">
                <span className="chip chip-bad mr-2">Likely blocker</span>
                <span className="text-ink">{r.title}</span>
                <span className="text-ink-soft">
                  {" "}
                  — {r.status === "CONFLICTING" ? "sources disagree" : "requirement not met"}
                </span>
              </li>
            ))}
            {risks.map((r) => (
              <li key={r.id} className="rounded-md border border-line bg-paper/60 p-3">
                <SeverityChip severity={r.severity} showRaw className="mr-2" />
                <span className="font-medium text-ink">{r.title}</span>
                <p className="mt-1 text-ink-soft">{r.reason}</p>
                <p className="mt-1 text-xs text-ink-faint">Next: {r.recommended_action}</p>
                {latestId && (
                  <div className="mt-2">
                    <RiskActions strategyId={latestId} riskId={r.id} status={r.status} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {risks.length > 0 && (
          <p className="mt-2 text-xs text-ink-faint">
            Plan-level risks apply to your whole strategy, not only this program.
          </p>
        )}
      </Section>

      {/* 5. Cost */}
      <Section index="05" title="Cost">
        {tuitionReq ? (
          <p className="text-sm">
            Tuition requirement from evidence:{" "}
            <strong className="text-ink">{formatValue(tuitionReq.value)}</strong>{" "}
            <StatusPill status={tuitionReq.status} />
          </p>
        ) : p.tuition_amount ? (
          <p className="text-sm">
            Listed tuition:{" "}
            <strong className="text-ink">
              {p.tuition_amount} {p.tuition_currency ?? ""}
            </strong>
            <span className="ml-2 text-xs text-ink-faint">
              stored value, not yet evidence-verified
            </span>
          </p>
        ) : (
          <p className="text-sm text-ink-faint">
            Not enough evidence yet — no tuition claim has been extracted from an official source.
          </p>
        )}
        {profile.data?.total_budget_amount && (
          <p className="mt-2 text-xs text-ink-faint">
            Your budget: {profile.data.total_budget_amount} {profile.data.budget_currency}
          </p>
        )}
      </Section>

      {/* 6. Deadline */}
      <Section index="06" title="Deadline">
        {deadlineReq ? (
          <p className="text-sm">
            <strong className="text-ink">{formatValue(deadlineReq.value)}</strong>{" "}
            <StatusPill status={deadlineReq.status} />
            <span className="ml-2 text-xs text-ink-faint">
              verified {fmtDate(deadlineReq.last_verified_at)}
            </span>
          </p>
        ) : (
          <p className="text-sm text-ink-faint">
            Not enough evidence yet — no application deadline has been verified for the selected
            intake. Use “Watch deadline” to get notified when monitoring finds one.
          </p>
        )}
      </Section>

      {/* 7. Career signal */}
      <Section index="07" title="Career signal">
        {ev.some((e) => e.claim_type === "career") ? (
          <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-ink">
            {ev
              .filter((e) => e.claim_type === "career")
              .map((e) => (
                <li key={e.id}>{e.claim}</li>
              ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-faint">
            Not enough evidence yet — career outcomes for this program have not been sourced from
            live job-market data.
          </p>
        )}
      </Section>

      {/* 8. Evidence */}
      <Section
        index="08"
        title="Evidence"
        aside={<span className="text-xs text-ink-faint">{ev.length} claims</span>}
      >
        {ev.length === 0 ? (
          <EmptyState
            title="No evidence yet"
            body="Claims for this program appear after a research run — start with the full example."
            action={
              <Link href="/research" className="btn-primary btn-sm">
                Run the full example
              </Link>
            }
          />
        ) : (
          <EvidenceList items={ev} />
        )}
      </Section>

      {/* 9. Next actions */}
      <Section index="09" title="Next actions">
        <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-ink">
          <li>
            <Link href="/research" className="link">
              Run live research
            </Link>{" "}
            to refresh this program&apos;s requirements and evidence.
          </li>
          {tasks.slice(0, 3).map((t) => (
            <li key={t.id}>
              {t.title}
              {t.due_date ? ` — due ${t.due_date}` : ""}
            </li>
          ))}
          {p.official_url && (
            <li>
              Verify on the{" "}
              <a href={p.official_url} target="_blank" rel="noreferrer" className="link">
                official page ↗
              </a>{" "}
              before applying — sources can change.
            </li>
          )}
        </ul>
      </Section>
    </main>
  );
}
