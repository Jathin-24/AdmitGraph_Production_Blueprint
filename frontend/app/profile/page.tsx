"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Term } from "../components/glossary";
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

type ProfileForm = Record<EditableKey, string>;

const TEXT_FIELDS: { key: EditableKey; label: string; hint: string }[] = [
  { key: "current_degree", label: "Highest degree", hint: "e.g. B.Tech" },
  { key: "field_of_study", label: "Field of study", hint: "e.g. Computer Science" },
  { key: "institution_name", label: "Institution", hint: "Your current university" },
  { key: "institution_country_code", label: "Study country (ISO)", hint: "e.g. DE" },
  { key: "career_goal", label: "Career goal", hint: "e.g. ML engineer in Germany" },
];

const FACTS = (p: ProfileOut): { id: string; label: React.ReactNode; value: string }[] => [
  {
    id: "cgpa",
    label: (
      <Term term="cgpa">CGPA</Term>
    ),
    value: p.cgpa ? `${p.cgpa}${p.cgpa_scale ? ` / ${p.cgpa_scale}` : ""}` : "Unknown",
  },
  {
    id: "percentage",
    label: (
      <Term term="cgpa">Percentage</Term>
    ),
    value: p.percentage ?? "Unknown",
  },
  { id: "backlogs", label: "Backlogs", value: p.backlogs !== null ? String(p.backlogs) : "Unknown" },
  {
    id: "experience",
    label: "Experience",
    value:
      p.total_experience_months !== null ? `${p.total_experience_months} months` : "Unknown",
  },
  {
    id: "budget",
    label: (
      <Term term="tuition_living">Budget</Term>
    ),
    value: p.total_budget_amount
      ? `${p.total_budget_amount} ${p.budget_currency ?? ""}`.trim()
      : "Unknown",
  },
  {
    id: "graduation_year",
    label: "Graduation year",
    value: p.graduation_year ? String(p.graduation_year) : "Unknown",
  },
];

/** Profile completion field → the onboarding step that collects it. */
const STEP_BY_MISSING_FIELD: Record<string, string> = {
  field_of_study: "goal",
  career_goal: "goal",
  preferred_countries: "goal",
  target_intakes: "goal",
  current_degree: "education",
  institution_name: "education",
  graduation_year: "education",
  cgpa: "education",
  total_budget_amount: "budget",
};

export default function ProfilePage() {
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });
  const [validation, setValidation] = useState<ValidationOut | null>(null);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Client-side validation mirrors only what the backend enforces:
  // institution_country_code is exactly 2 characters when provided
  // (backend/schemas/profile.py min_length=2, max_length=2). Every other
  // field is free text upstream, so we don't invent constraints for them.
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ProfileForm>({
    defaultValues: {
      current_degree: "",
      field_of_study: "",
      institution_name: "",
      institution_country_code: "",
      career_goal: "",
    },
    mode: "onTouched",
  });

  // Clear the "Saved." flash timer if the user navigates away mid-flash.
  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    []
  );

  useEffect(() => {
    if (profile.data) {
      reset({
        current_degree: profile.data.current_degree ?? "",
        field_of_study: profile.data.field_of_study ?? "",
        institution_name: profile.data.institution_name ?? "",
        institution_country_code: profile.data.institution_country_code ?? "",
        career_goal: profile.data.career_goal ?? "",
      });
    }
  }, [profile.data, reset]);

  const save = useMutation({
    mutationFn: (values: ProfileForm) => {
      const patch: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(values)) patch[k] = v === "" ? null : v;
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
  // Deep-link the guided setup to the step that fills the first missing fact.
  const missingStep =
    missing.length > 0 ? STEP_BY_MISSING_FIELD[missing[0]] ?? "goal" : null;

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
          <span className="label">
            <Term term="profile_completion">Profile completion</Term>
          </span>
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
            Missing: {missing.join(", ")} —{" "}
            <Link href={`/onboarding?step=${missingStep}`} className="underline hover:text-forest">
              fill these in the guided setup
            </Link>{" "}
            for sharper scoring.
          </p>
        )}
      </section>

      {/* Key facts */}
      <section aria-label="Key facts">
        <h2 className="display mb-3 text-lg font-medium">Key facts</h2>
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          {FACTS(p).map((fact) => (
            <div key={fact.id} className="card p-3">
              <p className="text-[11px] uppercase tracking-wide text-ink-faint">{fact.label}</p>
              <p
                className={
                  fact.value === "Unknown" ? "text-ink-faint" : "font-medium text-ink"
                }
              >
                {fact.value}
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
        <form noValidate onSubmit={handleSubmit((values) => save.mutate(values))}>
          <div className="grid gap-4 sm:grid-cols-2">
            {TEXT_FIELDS.map((f) => {
              const error = errors[f.key];
              const options =
                f.key === "institution_country_code"
                  ? {
                      validate: (value: string) =>
                        !value ||
                        value.trim().length === 2 ||
                        "Use the 2-letter country code, e.g. DE.",
                    }
                  : undefined;
              return (
                <label key={f.key} className="flex flex-col gap-1">
                  <span className="label">{f.label}</span>
                  <input
                    className="field"
                    placeholder={f.hint}
                    aria-invalid={error ? true : undefined}
                    {...register(f.key, options)}
                  />
                  {error && (
                    <span className="text-xs text-danger" role="alert">
                      {error.message}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={save.isPending}
              className="btn-primary"
            >
              {save.isPending ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
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
        </form>
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
