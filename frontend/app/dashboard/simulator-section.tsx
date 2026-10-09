"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Disclosure, ErrorNote } from "../components/ui";
import {
  simulateStrategy,
  type PortfolioMove,
  type SimulationResult,
} from "../lib/api";
import { CATEGORY_HEADING, type ProgramsQuery } from "./sections-shared";


const SCENARIOS = [
  "TOP_3_REJECTED",
  "BUDGET_MINUS_25_PERCENT",
  "IELTS_LOWERED",
  "REMOVE_COUNTRY",
  "DEADLINE_MISSED",
];

/** FRONTEND_SPEC §Failure simulator — student-facing scenario names. */
const SCENARIO_COPY: Record<string, string> = {
  TOP_3_REJECTED: "My top 3 reject me",
  BUDGET_MINUS_25_PERCENT: "My budget drops",
  IELTS_LOWERED: "My IELTS score is lower",
  REMOVE_COUNTRY: "I remove a country",
  DEADLINE_MISSED: "I miss the next deadline",
};

function scenarioLabel(scenario: string, country: string): string {
  const base = SCENARIO_COPY[scenario] ?? scenario.replaceAll("_", " ");
  return scenario === "REMOVE_COUNTRY" && country ? `${base}: ${country}` : base;
}

/* ------------------------------------------------- simulator profile dump */

function humanKey(key: string): string {
  return key.replaceAll("_", " ");
}

/** Readable scalar/object rendering for the simulator's modified profile —
 *  never JSON, and an unknown value stays visible as "Unknown". */
function plainValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.length > 0 ? value.map(plainValue).join(", ") : "Unknown";
  }
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>).map(
      ([k, v]) => `${humanKey(k)}: ${plainValue(v)}`
    );
    return parts.length > 0 ? parts.join(" · ") : "Unknown";
  }
  return String(value);
}

function MoveRow({ move }: { move: PortfolioMove }) {
  return (
    <li className="flex items-baseline gap-2 text-sm">
      <span className="display shrink-0 text-sm font-medium tabular-nums text-forest">
        {move.priority}
      </span>
      <Link href={`/programs/${move.program_id}`} className="link text-ink">
        {move.program_name ?? "Program"}
      </Link>
      <span className="chip chip-neutral ml-auto shrink-0">
        {CATEGORY_HEADING[move.category] ?? move.category.replace("_", " ")}
      </span>
    </li>
  );
}


export function SimulatorSection({
  selected,
  programs,
}: {
  selected: string | null;
  programs: ProgramsQuery;
}) {
  const queryClient = useQueryClient();

  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [removeCountry, setRemoveCountry] = useState("");
  const [sim, setSim] = useState<SimulationResult | null>(null);

  const countryOptions = useMemo(() => {
    const codes = new Set<string>();
    for (const p of programs.data?.items ?? []) {
      if (p.country_code) codes.add(p.country_code);
    }
    return Array.from(codes).sort();
  }, [programs.data]);

  const simulate = useMutation({
    mutationFn: () =>
      simulateStrategy(
        selected!,
        scenario,
        scenario === "REMOVE_COUNTRY" && removeCountry ? { country: removeCountry } : undefined
      ),
    onSuccess: (out) => {
      setSim(out);
      queryClient.invalidateQueries({ queryKey: ["strategy", selected] });
    },
    onError: (err) =>
      setSim({
        scenario,
        error: err instanceof Error ? err.message : "The simulation could not be run.",
      }),
  });

  /* ------------------------------------------------------------- simulator */
  const hasPortfolioDelta =
    !!sim &&
    !sim.error &&
    ((sim.portfolio_before?.length ?? 0) > 0 ||
      (sim.portfolio_after?.length ?? 0) > 0 ||
      !!sim.delta);
  const deltaGroups =
    sim?.delta &&
    [
      { label: "Moved up", glyph: "↑", cls: "chip-good", items: sim.delta.moved_up ?? [] },
      { label: "Moved down", glyph: "↓", cls: "chip-warn", items: sim.delta.moved_down ?? [] },
      { label: "Added", glyph: "+", cls: "chip-good", items: sim.delta.added ?? [] },
      { label: "Removed", glyph: "−", cls: "chip-bad", items: sim.delta.removed ?? [] },
    ].filter((g) => g.items.length > 0);


  return (
    <>

          {/* Simulator — folded by default: a "what if" tool, not something a
              student needs on their first read of the plan. */}
          <Disclosure summary="What could break this plan?" hint="Stress-test the plan">
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="label">Scenario</span>
                <select
                  value={scenario}
                  onChange={(e) => {
                    setScenario(e.target.value);
                    setSim(null);
                  }}
                  className="field w-auto"
                >
                  {SCENARIOS.map((s) => (
                    <option key={s} value={s}>
                      {scenarioLabel(s, "")}
                    </option>
                  ))}
                </select>
              </label>

              {scenario === "REMOVE_COUNTRY" && (
                <label className="flex flex-col gap-1">
                  <span className="label">Country to remove</span>
                  <select
                    value={removeCountry}
                    onChange={(e) => {
                      setRemoveCountry(e.target.value);
                      setSim(null);
                    }}
                    className="field w-auto"
                    required
                  >
                    <option value="">Choose a country…</option>
                    {countryOptions.map((code) => (
                      <option key={code} value={code}>
                        {code}
                      </option>
                    ))}
                    {countryOptions.length === 0 && programs.isLoading && (
                      <option value="" disabled>
                        Loading countries…
                      </option>
                    )}
                  </select>
                </label>
              )}

              <button
                onClick={() => simulate.mutate()}
                disabled={
                  simulate.isPending ||
                  (scenario === "REMOVE_COUNTRY" && !removeCountry)
                }
                className="btn-primary self-end"
              >
                {simulate.isPending ? "Simulating…" : "Simulate"}
              </button>
            </div>

            {scenario === "REMOVE_COUNTRY" && !removeCountry && (
              <p className="mt-2 text-xs text-ink-faint">
                Pick the country to drop and we will re-score your portfolio without it.
              </p>
            )}

            {programs.isError && scenario === "REMOVE_COUNTRY" && (
              <div className="mt-3">
                <ErrorNote message="Country list unavailable — we could not load your programs. The rest of this simulator still works." />
              </div>
            )}

            {sim?.error && (
              <div className="mt-3">
                <ErrorNote message={`Simulation failed: ${sim.error}`} />
              </div>
            )}

            {hasPortfolioDelta && sim && (
              <div className="mt-4 space-y-3">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="card p-3">
                    <p className="eyebrow mb-2">Before</p>
                    <ul className="flex flex-col gap-2">
                      {(sim.portfolio_before ?? []).map((m) => (
                        <MoveRow key={`b-${m.program_id}`} move={m} />
                      ))}
                      {(sim.portfolio_before ?? []).length === 0 && (
                        <li className="text-sm text-ink-faint">Not available.</li>
                      )}
                    </ul>
                  </div>
                  <div className="card p-3">
                    <p className="eyebrow mb-2">After</p>
                    <ul className="flex flex-col gap-2">
                      {(sim.portfolio_after ?? []).map((m) => (
                        <MoveRow key={`a-${m.program_id}`} move={m} />
                      ))}
                      {(sim.portfolio_after ?? []).length === 0 && (
                        <li className="text-sm text-ink-faint">Not available.</li>
                      )}
                    </ul>
                  </div>
                </div>

                {deltaGroups && deltaGroups.length > 0 && (
                  <div className="rounded-lg border border-line bg-paper/60 p-3">
                    <p className="eyebrow mb-2">What moved</p>
                    <ul className="flex flex-col gap-1.5 text-sm">
                      {deltaGroups.map((g) => (
                        <li key={g.label} className="flex flex-wrap items-baseline gap-2">
                          <span className={`chip ${g.cls}`}>
                            {g.glyph} {g.items.length}
                          </span>
                          <span className="font-medium text-ink">{g.label}</span>
                          <span className="text-xs text-ink-soft">
                            {g.items.map((m) => m.program_name ?? "Program").join(", ")}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {sim.delta?.summary && (
                      <p className="mt-2 border-t border-line pt-2 text-sm text-ink">
                        {sim.delta.summary}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {!hasPortfolioDelta && sim && !sim.error && sim.modified_profile && (
              <div className="mt-3 rounded-md border border-line bg-paper/60 p-3">
                <p className="eyebrow mb-2">
                  Scenario {scenarioLabel(sim.scenario, removeCountry)} applied to your profile
                </p>
                {Object.keys(sim.modified_profile).length === 0 ? (
                  <p className="text-xs text-ink-faint">
                    This scenario changed no stored profile values.
                  </p>
                ) : (
                  <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                    {Object.entries(sim.modified_profile).map(([key, value]) => (
                      <div key={key} className="flex gap-2 text-xs">
                        <dt className="shrink-0 text-ink-faint">{humanKey(key)}</dt>
                        <dd className="min-w-0 break-words text-ink-soft">{plainValue(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>
            )}

            <p className="mt-2 text-xs text-ink-faint">
              Simulations recompute fit against your profile — they never predict admission
              outcomes.
            </p>
          </Disclosure>

    </>
  );
}
