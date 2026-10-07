"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
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

export default function ProfilePage() {
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });
  const [form, setForm] = useState<Record<string, string>>({});
  const [validation, setValidation] = useState<ValidationOut | null>(null);
  const [saved, setSaved] = useState(false);

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
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const validate = useMutation({ mutationFn: validateProfile, onSuccess: setValidation });

  if (profile.isLoading) return <main className="p-6 text-neutral-500">Loading profile…</main>;
  if (profile.isError) {
    return (
      <main className="p-6" role="alert">
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm">
          Could not load your profile: {(profile.error as Error).message}.
        </div>
      </main>
    );
  }

  const p: ProfileOut = profile.data!;
  const pct = completion.data?.profile_completion ?? 0;
  const missing = completion.data?.missing_fields ?? [];

  return (
    <main className="mx-auto max-w-3xl p-6">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Your profile</h1>
          <p className="text-sm text-neutral-500">
            The facts your strategy is scored against — every recommendation traces back to these.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/onboarding" className="rounded-full border px-4 py-1.5 text-sm hover:border-black">
            Guided setup
          </Link>
          <Link href="/research" className="rounded-full bg-black px-4 py-1.5 text-sm text-white">
            Run research
          </Link>
        </div>
      </div>

      {/* Completion */}
      <section className="mb-6 rounded-xl border border-neutral-200 p-4" aria-label="Profile completion">
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="font-medium">Profile completion</span>
          <span>{pct}%</span>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded bg-neutral-100"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="h-full bg-black transition-all" style={{ width: `${pct}%` }} />
        </div>
        {missing.length > 0 && (
          <p className="mt-2 text-xs text-neutral-500">
            Missing: {missing.join(", ")} — fill these in the guided setup for sharper scoring.
          </p>
        )}
      </section>

      {/* Key facts */}
      <section className="mb-6 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3" aria-label="Key facts">
        {[
          ["CGPA", p.cgpa ? `${p.cgpa}${p.cgpa_scale ? ` / ${p.cgpa_scale}` : ""}` : "Unknown"],
          ["Percentage", p.percentage ?? "Unknown"],
          ["Backlogs", p.backlogs !== null ? String(p.backlogs) : "Unknown"],
          ["Experience", p.total_experience_months !== null ? `${p.total_experience_months} months` : "Unknown"],
          ["Budget", p.total_budget_amount ? `${p.total_budget_amount} ${p.budget_currency ?? ""}`.trim() : "Unknown"],
          ["Graduation year", p.graduation_year ? String(p.graduation_year) : "Unknown"],
        ].map(([label, value]) => (
          <div key={label} className="rounded border border-neutral-100 p-3">
            <p className="text-xs text-neutral-400">{label}</p>
            <p className={value === "Unknown" ? "text-neutral-400" : "font-medium"}>{value}</p>
          </div>
        ))}
      </section>

      {/* Edit */}
      <section className="mb-6 rounded-xl border border-neutral-200 p-4" aria-label="Edit profile">
        <h2 className="mb-3 font-medium">Edit quick facts</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {TEXT_FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1 text-sm">
              {f.label}
              <input
                className="rounded border p-2"
                placeholder={f.hint}
                value={form[f.key] ?? ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-full bg-black px-5 py-2 text-sm text-white"
          >
            Save changes
          </button>
          <button
            onClick={() => validate.mutate()}
            disabled={validate.isPending}
            className="rounded-full border px-5 py-2 text-sm hover:border-black"
          >
            Validate profile
          </button>
          {saved && (
            <span role="status" className="text-sm text-green-700">
              Saved.
            </span>
          )}
          {save.isError && (
            <span role="alert" className="text-sm text-red-600">
              {(save.error as Error).message}
            </span>
          )}
        </div>
        {validation && (
          <div className="mt-3 text-sm" role="status">
            {validation.valid ? (
              <p className="text-green-700">Profile looks valid ✓</p>
            ) : (
              <ul className="flex list-disc flex-col gap-1 pl-5 text-red-600">
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

      <p className="text-xs text-neutral-400">
        Your data stays in your profile — it is never sent to any provider, and no admission
        probability is ever computed from it.
      </p>
    </main>
  );
}
