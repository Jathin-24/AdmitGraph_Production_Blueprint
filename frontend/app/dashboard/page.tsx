"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  getStrategies,
  getStrategy,
  listEvidence,
  simulateStrategy,
  type EvidenceItem,
} from "../lib/api";

const CATEGORY_ORDER = ["REACH", "TARGET", "LOWER_RISK"];
const SCENARIOS = [
  "TOP_3_REJECTED",
  "BUDGET_MINUS_25_PERCENT",
  "IELTS_LOWERED",
  "REMOVE_COUNTRY",
  "DEADLINE_MISSED",
];

function severityClass(severity: string): string {
  if (severity === "CRITICAL") return "bg-red-100 text-red-700";
  if (severity === "HIGH") return "bg-orange-100 text-orange-700";
  if (severity === "MEDIUM") return "bg-yellow-100 text-yellow-800";
  return "bg-neutral-100 text-neutral-700";
}

function EvidenceDrawer({ programId, onClose }: { programId: string; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["evidence", programId],
    queryFn: () => listEvidence(programId),
  });
  return (
    <div className="rounded border p-4" role="region" aria-label="Evidence">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-medium">Evidence</h3>
        <button onClick={onClose} aria-label="Close evidence" className="text-sm underline">
          Close
        </button>
      </div>
      {(data?.items ?? []).length === 0 ? (
        <p className="text-sm text-neutral-500">
          No verified evidence yet for this program — claims appear after a live research run
          extracts them.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {(data?.items ?? []).map((e: EvidenceItem) => (
            <li key={e.id} className="text-sm">
              <span className="mr-2 rounded bg-neutral-100 px-1.5 py-0.5 text-xs">
                {e.confidence}
              </span>
              {e.claim}
              <span className="ml-2 text-xs text-neutral-400">{e.status}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [simResult, setSimResult] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });
  const detail = useQuery({
    queryKey: ["strategy", selected],
    queryFn: () => getStrategy(selected!),
    enabled: !!selected,
  });

  const simulate = useMutation({
    mutationFn: () => simulateStrategy(selected!, scenario),
    onSuccess: (out) => {
      setSimResult(
        out.error ?? `Scenario ${out.scenario} simulated: ${JSON.stringify(out.modified_profile)}`
      );
      queryClient.invalidateQueries({ queryKey: ["strategy", selected] });
    },
    onError: (err) => setSimResult(err instanceof Error ? err.message : "Simulation failed"),
  });

  const items = strategies.data?.items ?? [];

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-10">
      <h1 className="text-2xl font-semibold">Strategy dashboard</h1>

      <section aria-label="Strategies" className="flex flex-wrap gap-3">
        {items.length === 0 && (
          <p className="text-neutral-600">
            No strategies yet. Run live research first from the{" "}
            <a className="underline" href="/research">
              research page
            </a>
            .
          </p>
        )}
        {items.map((s) => (
          <button
            key={s.id}
            onClick={() => {
              setSelected(s.id);
              setEvidenceFor(null);
            }}
            className={`rounded-full border px-4 py-2 text-sm ${
              selected === s.id ? "border-black bg-black text-white" : ""
            }`}
          >
            {s.created_at.slice(0, 10)} · health {s.plan_health_score ?? "–"}
          </button>
        ))}
      </section>

      {detail.data && (
        <>
          <section aria-label="Portfolio" className="grid gap-4 sm:grid-cols-3">
            {CATEGORY_ORDER.map((cat) => (
              <div key={cat} className="rounded border p-4">
                <h2 className="mb-2 font-medium">{cat.replace("_", " ")}</h2>
                <ul className="flex flex-col gap-3">
                  {detail.data.portfolio
                    .filter((p) => p.category === cat)
                    .map((p) => (
                      <li key={p.program_id} className="text-sm">
                        <div className="font-medium">
                          {p.priority}. {p.program_name ?? "Program"}
                        </div>
                        <div className="text-neutral-500">{p.institution}</div>
                        <div className="text-xs text-neutral-500">{p.rationale}</div>
                        <button
                          className="mt-1 text-xs underline"
                          onClick={() =>
                            setEvidenceFor(evidenceFor === p.program_id ? null : p.program_id)
                          }
                        >
                          Evidence
                        </button>
                      </li>
                    ))}
                  {detail.data.portfolio.filter((p) => p.category === cat).length === 0 && (
                    <li className="text-sm text-neutral-400">None in this tier.</li>
                  )}
                </ul>
              </div>
            ))}
          </section>

          {evidenceFor && (
            <EvidenceDrawer programId={evidenceFor} onClose={() => setEvidenceFor(null)} />
          )}

          <section aria-label="Risks" className="rounded border p-4">
            <h2 className="mb-3 font-medium">Risks</h2>
            {detail.data.risks.length === 0 ? (
              <p className="text-sm text-neutral-500">No risks flagged.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {detail.data.risks.map((r) => (
                  <li key={r.id} className="text-sm">
                    <span
                      className={`mr-2 rounded px-1.5 py-0.5 text-xs ${severityClass(r.severity)}`}
                    >
                      {r.severity}
                    </span>
                    <span className="font-medium">{r.title}</span>
                    <div className="text-neutral-600">{r.reason}</div>
                    <div className="text-neutral-500">→ {r.recommended_action}</div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="Roadmap" className="rounded border p-4">
            <h2 className="mb-3 font-medium">Next tasks</h2>
            <ul className="flex flex-col gap-2">
              {detail.data.roadmap_tasks.map((t) => (
                <li key={t.id} className="text-sm">
                  ○ {t.title} {t.due_date ? `· due ${t.due_date}` : ""}
                </li>
              ))}
              {detail.data.roadmap_tasks.length === 0 && (
                <li className="text-sm text-neutral-400">No tasks generated yet.</li>
              )}
            </ul>
          </section>

          <section aria-label="Simulator" className="rounded border p-4">
            <h2 className="mb-3 font-medium">What-if simulator</h2>
            <div className="flex flex-wrap items-center gap-3">
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                className="rounded border p-2"
                aria-label="Scenario"
              >
                {SCENARIOS.map((s) => (
                  <option key={s} value={s}>
                    {s.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <button
                onClick={() => simulate.mutate()}
                disabled={simulate.isPending}
                className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
              >
                {simulate.isPending ? "Simulating…" : "Simulate"}
              </button>
            </div>
            {simResult && <pre className="mt-3 overflow-x-auto text-xs">{simResult}</pre>}
          </section>
        </>
      )}
    </main>
  );
}
