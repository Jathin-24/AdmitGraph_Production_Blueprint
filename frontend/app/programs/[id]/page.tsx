"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  createSubscription,
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

const STATUS_COPY: Record<string, { label: string; cls: string }> = {
  SATISFIED: { label: "Looks good", cls: "bg-green-100 text-green-800" },
  PARTIAL: { label: "Needs verification", cls: "bg-yellow-100 text-yellow-800" },
  NOT_SATISFIED: { label: "Likely blocker", cls: "bg-red-100 text-red-700" },
  NOT_APPLICABLE: { label: "Not required", cls: "bg-neutral-100 text-neutral-600" },
  UNKNOWN: { label: "Not enough evidence yet", cls: "bg-neutral-100 text-neutral-500" },
  CONFLICTING: { label: "Sources disagree — verify", cls: "bg-orange-100 text-orange-700" },
  NEEDS_VERIFICATION: { label: "Needs verification", cls: "bg-yellow-100 text-yellow-800" },
};

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

function statusCopy(status: string) {
  return STATUS_COPY[status] ?? STATUS_COPY.UNKNOWN;
}

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
    return `${String(value.amount)} ${value.currency ? String(value.currency) : ""}`.trim();
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-200 p-5" aria-label={title}>
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function ProgramDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const queryClient = useQueryClient();

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
    queryKey: ["saved-programs"],
    queryFn: async () => {
      const out = await listPrograms(1, 100, true);
      return new Set(out.items.map((sp) => sp.id));
    },
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
    return <main className="p-6 text-neutral-500">Loading program…</main>;
  }
  if (program.isError) {
    return (
      <main className="p-6" role="alert">
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm">
          Could not load this program: {(program.error as Error).message}.
        </div>
      </main>
    );
  }

  const p = program.data!;
  const reqs = requirements.data?.items ?? [];
  const ev = evidence.data?.items ?? [];
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
    <main className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-neutral-500">
            {p.institution ?? "Institution unknown"}
            {p.city ? ` · ${p.city}` : ""}
            {p.country_code ? ` · ${p.country_code}` : ""}
          </p>
          <h1 className="text-2xl font-semibold">{p.name}</h1>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className={`rounded-full border px-4 py-1.5 text-sm ${
              isSaved ? "border-black bg-black text-white" : "border-neutral-300 hover:border-black"
            }`}
          >
            {isSaved ? "Saved ✓" : "Save to my plan"}
          </button>
          <button
            onClick={() => watch.mutate()}
            disabled={watch.isPending}
            className="rounded-full border border-neutral-300 px-4 py-1.5 text-sm hover:border-black"
          >
            {watch.isSuccess ? "Watching ✓" : "Watch deadline"}
          </button>
        </div>
      </div>
      {watch.isSuccess && (
        <p className="text-sm text-green-700" role="status">
          Weekly deadline monitoring started — see the{" "}
          <Link href="/monitor" className="underline">
            Monitor page
          </Link>
          .
        </p>
      )}

      {/* 1. Overview */}
      <Section title="Overview">
        <div className="flex flex-wrap gap-1.5 text-xs">
          {[p.degree_type, p.field_of_study, p.specialization, p.language, p.active ? "Active" : "Inactive"]
            .filter(Boolean)
            .map((b) => (
              <span key={String(b)} className="rounded bg-neutral-100 px-2 py-1">
                {String(b)}
              </span>
            ))}
          {p.duration_months && (
            <span className="rounded bg-neutral-100 px-2 py-1">{p.duration_months} months</span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-4 text-sm">
          {p.official_url && (
            <a href={p.official_url} target="_blank" rel="noreferrer" className="underline">
              Official program page ↗
            </a>
          )}
          <span className="text-neutral-500">
            {p.requirement_count} requirements · {p.evidence_count} evidence claims
          </span>
          {p.last_verified_at && (
            <span className="text-neutral-500">
              Last verified {new Date(p.last_verified_at).toLocaleDateString()}
            </span>
          )}
        </div>
      </Section>

      {/* 2. Why it fits */}
      <Section title="Why it fits">
        {fitItem ? (
          <div>
            <p className="mb-1 text-2xl font-semibold">{fitItem.overall_score} / 100 fit</p>
            <p className="text-sm text-neutral-600">
              {fitItem.explanation ?? "Score computed from your stored profile and this program's evidence."}
            </p>
            <p className="mt-2 text-xs text-neutral-400">
              This is an explainable product score, not an admission probability.
            </p>
          </div>
        ) : (
          <p className="text-sm text-neutral-600">
            {reqs.length > 0
              ? `${satisfied} of ${reqs.length} known requirements look satisfied so far. Fit scoring appears after a research run completes for your profile.`
              : "Not enough evidence yet — fit scoring appears after a research run extracts this program's requirements."}
          </p>
        )}
      </Section>

      {/* 3. Eligibility matrix */}
      <Section title="Eligibility matrix">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase text-neutral-500">
                <th className="py-2 pr-3">Area</th>
                <th className="py-2 pr-3">You</th>
                <th className="py-2 pr-3">Requirement</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {CATEGORIES.map((cat) => {
                const catReqs = reqs.filter(
                  (r) => cat.types.includes(r.requirement_type) || cat.keys.includes(r.normalized_key)
                );
                const status = worstStatus(catReqs);
                const copy = statusCopy(status);
                const catEv = ev.filter((e) => e.normalized_claim && cat.keys.includes(e.normalized_claim));
                return (
                  <tr key={cat.label} className="border-b border-neutral-100 align-top">
                    <td className="py-2.5 pr-3 font-medium">{cat.label}</td>
                    <td className="py-2.5 pr-3 text-neutral-600">{youColumn(cat.label, profile.data ?? null)}</td>
                    <td className="py-2.5 pr-3 text-neutral-600">
                      {catReqs.length > 0
                        ? catReqs.map((r) => formatValue(r.value)).join(" · ")
                        : "No requirement evidence yet"}
                    </td>
                    <td className="py-2.5 pr-3">
                      <span className={`rounded px-2 py-1 text-xs ${copy.cls}`}>{copy.label}</span>
                    </td>
                    <td className="py-2.5 text-xs text-neutral-500">
                      {catEv.length > 0
                        ? `${catEv.length} claim${catEv.length > 1 ? "s" : ""} (${Array.from(new Set(catEv.map((e) => e.confidence))).join(", ")})`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      {/* 4. Risks */}
      <Section title="Risks">
        {blockers.length === 0 && risks.length === 0 ? (
          <p className="text-sm text-neutral-500">No open risks flagged for this program yet.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {blockers.map((r) => (
              <li key={r.id} className="rounded border border-red-100 bg-red-50 p-3">
                <span className="mr-2 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700">
                  Likely blocker
                </span>
                {r.title} — {r.status === "CONFLICTING" ? "sources disagree" : "requirement not met"}
              </li>
            ))}
            {risks.map((r) => (
              <li key={r.id} className="rounded border p-3">
                <span className="mr-2 rounded bg-neutral-100 px-1.5 py-0.5 text-xs">{r.severity}</span>
                <span className="font-medium">{r.title}</span>
                <p className="mt-1 text-neutral-600">{r.reason}</p>
                <p className="mt-1 text-neutral-500">Next: {r.recommended_action}</p>
              </li>
            ))}
          </ul>
        )}
        {risks.length > 0 && (
          <p className="mt-2 text-xs text-neutral-400">
            Plan-level risks apply to your whole strategy, not only this program.
          </p>
        )}
      </Section>

      {/* 5. Cost */}
      <Section title="Cost">
        {tuitionReq ? (
          <p className="text-sm">
            Tuition requirement from evidence: <strong>{formatValue(tuitionReq.value)}</strong>{" "}
            <span className={`ml-2 rounded px-2 py-0.5 text-xs ${statusCopy(tuitionReq.status).cls}`}>
              {statusCopy(tuitionReq.status).label}
            </span>
          </p>
        ) : p.tuition_amount ? (
          <p className="text-sm">
            Listed tuition: <strong>{p.tuition_amount} {p.tuition_currency ?? ""}</strong>
            <span className="ml-2 text-xs text-neutral-400">stored value, not yet evidence-verified</span>
          </p>
        ) : (
          <p className="text-sm text-neutral-500">
            Not enough evidence yet — no tuition claim has been extracted from an official source.
          </p>
        )}
        {profile.data?.total_budget_amount && (
          <p className="mt-2 text-xs text-neutral-500">
            Your budget: {profile.data.total_budget_amount} {profile.data.budget_currency}
          </p>
        )}
      </Section>

      {/* 6. Deadline */}
      <Section title="Deadline">
        {deadlineReq ? (
          <p className="text-sm">
            <strong>{formatValue(deadlineReq.value)}</strong>{" "}
            <span className={`ml-1 rounded px-2 py-0.5 text-xs ${statusCopy(deadlineReq.status).cls}`}>
              {statusCopy(deadlineReq.status).label}
            </span>
            {deadlineReq.last_verified_at && (
              <span className="ml-2 text-xs text-neutral-400">
                verified {new Date(deadlineReq.last_verified_at).toLocaleDateString()}
              </span>
            )}
          </p>
        ) : (
          <p className="text-sm text-neutral-500">
            Not enough evidence yet — no application deadline has been verified for the selected
            intake. Use “Watch deadline” to get notified when monitoring finds one.
          </p>
        )}
      </Section>

      {/* 7. Career signal */}
      <Section title="Career signal">
        {ev.some((e) => e.claim_type === "career") ? (
          <ul className="flex flex-col gap-1 text-sm">
            {ev
              .filter((e) => e.claim_type === "career")
              .map((e) => (
                <li key={e.id}>{e.claim}</li>
              ))}
          </ul>
        ) : (
          <p className="text-sm text-neutral-500">
            Not enough evidence yet — career outcomes for this program haven&apos;t been sourced
            from live job-market data.
          </p>
        )}
      </Section>

      {/* 8. Evidence */}
      <Section title="Evidence">
        {ev.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No evidence claims yet for this program — they appear after a live research run.
          </p>
        ) : (
          <EvidenceList items={ev} />
        )}
      </Section>

      {/* 9. Next actions */}
      <Section title="Next actions">
        <ul className="flex list-disc flex-col gap-2 pl-5 text-sm">
          <li>
            <Link href="/research" className="underline">
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
              <a href={p.official_url} target="_blank" rel="noreferrer" className="underline">
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

function EvidenceList({ items }: { items: EvidenceItem[] }) {
  return (
    <ul className="flex flex-col gap-3">
      {items.map((e) => (
        <li key={e.id} className="rounded border border-neutral-100 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs">{e.confidence}</span>
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs">{e.status}</span>
            <span className="text-xs text-neutral-400">{e.claim_type}</span>
          </div>
          <p className="mt-1.5">{e.claim}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-neutral-500">
            {e.source_domain && <span>Source: {e.source_domain}</span>}
            {e.source_authority && <span>Authority: {e.source_authority}</span>}
            {e.retrieved_at && <span>Retrieved: {new Date(e.retrieved_at).toLocaleDateString()}</span>}
            {e.freshness_deadline && (
              <span>
                Fresh until: {new Date(e.freshness_deadline).toLocaleDateString()}
              </span>
            )}
            {e.source_url && (
              <a href={e.source_url} target="_blank" rel="noreferrer" className="underline">
                Open source ↗
              </a>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
