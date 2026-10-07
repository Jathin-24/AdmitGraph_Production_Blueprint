"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import {
  getCompletion,
  getProfile,
  updateProfile,
  validateProfile,
  type ProfileOut,
  type ValidationOut,
} from "../lib/api";

type EditableKey =
  | "current_degree"
  | "field_of_study"
  | "institution_name"
  | "institution_country_code"
  | "career_goal";

const TEXT_FIELDS: { key: EditableKey; label: string; hint: string }[] = [
  { key: "current_degree", label: "Highest degree", hint: "e.g. B.Tech" },
  { key: "field_of_study", label: "Field of study", hint: "e.g. Computer Science" },
  { key: "institution_name", label: "Institution", hint: "Your current university" },
  { key: "institution_country_code", label: "Study country (ISO)", hint: "e.g. DE" },
  { key: "career_goal", label: "Career goal", hint: "e.g. ML engineer in Germany" },
];

const FACTS = (p: ProfileOut): [string, string][] => [
  ["CGPA", p.cgpa ? `${p.cgpa}${p.cgpa_scale ? ` / ${p.cgpa_scale}` : ""}` : "Unknown"],
  ["Percentage", p.percentage ?? "Unknown"],
  ["Backlogs", p.backlogs !== null ? String(p.backlogs) : "Unknown"],
  [
    "Experience",
    p.total_experience_months !== null ? `${p.total_experience_months} months` : "Unknown",
  ],
  [
    "Budget",
    p.total_budget_amount
      ? `${p.total_budget_amount} ${p.budget_currency ?? ""}`.trim()
      : "Unknown",
  ],
  ["Graduation year", p.graduation_year ? String(p.graduation_year) : "Unknown"],
];

export default function ProfilePage() {
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });
  const [form, setForm] = useState<Record<string, string>>({});
  const [validation, setValidation] = useState<ValidationOut | null>(null);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear the "Saved." flash timer if the user navigates away mid-flash.
  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    []
  );

  useEffect(() => {
    if (profile.data) {
      setForm({
        current_degree: profile.data.current_degree ?? "",
        field_of_study: profile.data.field_of_study ?? "",
        institution_name: profile.data.institution_name ?? "",
        institution_country_code: profile.data.institution_country_code ?? "",
        career_goal: profile.data.career_goal ?? "",
      });
    }
  }, [profile.data]);

  const save = useMutation({
    mutationFn: () => {
      const patch: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) patch[k] = v === "" ? null : v;
      return updateProfile(patch);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["completion"] });
      setSaved(true);
      if (savedTimer.current) clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaved(false), 2500);
    },
  });

  const validate = useMutation({ mutationFn: validateProfile, onSuccess: setValidation });

  if (profile.isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-5 py-8">
        <LoadingNote what="Loading profile…" />
      </main>
    );
  }
  if (profile.isError) {
    return (
      <main className="mx-auto max-w-3xl px-5 py-8">
        <ErrorNote message={`Could not load your profile: ${(profile.error as Error).message}.`} />
      </main>
    );
  }

  const p: ProfileOut = profile.data!;
  const pct = completion.data?.profile_completion ?? 0;
  const missing = completion.data?.missing_fields ?? [];

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Your data"
        title="Your profile"
        lede="The facts your strategy is scored against — every recommendation traces back to these."
        actions={
          <>
            <Link href="/onboarding" className="btn-secondary">
              Guided setup
            </Link>
            <Link href="/research" className="btn-primary">
              Run research
            </Link>
          </>
        }
      />

      {/* Completion */}
      <section className="card p-5" aria-label="Profile completion">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="label">Profile completion</span>
          <span className="display text-2xl font-medium tabular-nums text-forest">{pct}%</span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded bg-paper-dark"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="h-full bg-forest transition-all" style={{ width: `${pct}%` }} />
        </div>
        {missing.length > 0 && (
          <p className="mt-2 text-xs text-ink-faint">
            Missing: {missing.join(", ")} — fill these in the guided setup for sharper scoring.
          </p>
        )}
      </section>

      {/* Key facts */}
      <section aria-label="Key facts">
        <h2 className="display mb-3 text-lg font-medium">Key facts</h2>
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          {FACTS(p).map(([label, value]) => (
            <div key={label} className="card p-3">
              <p className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</p>
              <p
                className={
                  value === "Unknown" ? "text-ink-faint" : "font-medium text-ink"
                }
              >
                {value}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Edit */}
      <section className="card p-5" aria-label="Edit profile">
        <h2 className="display mb-4 border-b border-line pb-3 text-lg font-medium">
          Edit quick facts
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {TEXT_FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1">
              <span className="label">{f.label}</span>
              <input
                className="field"
                placeholder={f.hint}
                value={form[f.key] ?? ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button onClick={() => save.mutate()} disabled={save.isPending} className="btn-primary">
            Save changes
          </button>
          <button
            onClick={() => validate.mutate()}
            disabled={validate.isPending}
            className="btn-secondary"
          >
            Validate profile
          </button>
          {saved && (
            <span role="status" className="text-sm text-forest">
              Saved.
            </span>
          )}
          {save.isError && <ErrorNote message={(save.error as Error).message} />}
        </div>
        {validation && (
          <div className="mt-3 text-sm" role="status">
            {validation.valid ? (
              <p className="text-forest">Profile looks valid ✓</p>
            ) : (
              <ul className="flex list-disc flex-col gap-1 pl-5 text-danger">
                {validation.issues.map((i) => (
                  <li key={`${i.field}-${i.message}`}>
                    {i.field}: {i.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <p className="text-xs text-ink-faint">
        Your data stays in your profile — it is never sent to any provider, and no admission
        probability is ever computed from it.
      </p>
    </main>
  );
}
