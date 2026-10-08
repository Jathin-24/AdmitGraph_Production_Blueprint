"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Term, type GlossaryKey } from "../components/glossary";
import { LoadingNote } from "../components/ui";
import {
  getOnboardingProgress,
  saveOnboardingAnswers,
  type OnboardingProgress,
} from "../lib/api";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** Onboarding keys that mirror profile fields — prefilled from GET /me/profile. */
const PREFILL_KEYS = [
  "field_of_study",
  "career_goal",
  "current_degree",
  "institution_name",
  "cgpa",
  "graduation_year",
  "total_budget_amount",
];

interface Field {
  key: string;
  question: string;
  explanation: string;
  example?: string;
  why_we_ask?: string;
  input_type: string;
  options?: string[];
  required?: boolean;
}
interface Step {
  id: string;
  title: string;
  fields: Field[];
}

/** Whole-number inputs (integer columns); every other number accepts decimals. */
const INTEGER_KEYS = new Set(["graduation_year", "backlogs", "total_experience_months"]);

/** One decision per screen: tightly related fields share a card. */
const FIELD_GROUPS: Record<string, { title: string; keys: string[] }[]> = {
  goal: [
    { title: "What you want to study", keys: ["field_of_study", "career_goal"] },
    { title: "Where and when", keys: ["preferred_countries", "target_intakes"] },
  ],
  education: [
    { title: "Your degree", keys: ["current_degree", "institution_name", "graduation_year"] },
    { title: "Your grades", keys: ["cgpa", "cgpa_scale", "percentage", "backlogs"] },
  ],
  tests: [{ title: "English test", keys: ["english_test_overall"] }],
  experience: [{ title: "Work so far", keys: ["total_experience_months", "skills"] }],
  budget: [
    {
      title: "Money",
      keys: ["total_budget_amount", "annual_budget_amount", "scholarship_dependence"],
    },
  ],
  preferences: [
    {
      title: "Degree and place",
      keys: ["preferred_degree_types", "preferred_cities", "excluded_countries"],
    },
    {
      title: "What matters after the degree",
      keys: ["career_market_importance", "research_preference"],
    },
  ],
};

/** Glossary popovers for the review summary (education-first wording). */
const REVIEW_TERMS: Record<string, GlossaryKey> = {
  current_degree: "masters_degree",
  target_intakes: "intake",
  cgpa: "cgpa",
  percentage: "cgpa",
  total_budget_amount: "tuition_living",
  english_test_overall: "language_requirement",
};

function groupsFor(step: Step): { title: string; fields: Field[] }[] {
  const spec = FIELD_GROUPS[step.id];
  if (!spec) return [{ title: step.title, fields: step.fields }];
  const byKey = new Map(step.fields.map((f) => [f.key, f]));
  const groups: { title: string; fields: Field[] }[] = [];
  const used = new Set<string>();
  for (const group of spec) {
    const fields: Field[] = [];
    for (const key of group.keys) {
      const field = byKey.get(key);
      if (field) {
        fields.push(field);
        used.add(key);
      }
    }
    if (fields.length > 0) groups.push({ title: group.title, fields });
  }
  const rest = step.fields.filter((f) => !used.has(f.key));
  if (rest.length > 0) groups.push({ title: "A few more details", fields: rest });
  return groups.length > 0 ? groups : [{ title: step.title, fields: step.fields }];
}

/** Per-step validation: required must be present, numbers must parse. */
function validateStep(step: Step, answers: Record<string, string>): Record<string, string> {
  const found: Record<string, string> = {};
  for (const field of step.fields) {
    const value = (answers[field.key] ?? "").trim();
    if (!value) {
      if (field.required) found[field.key] = "This one is required before you continue.";
      continue;
    }
    if (field.input_type === "number") {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        found[field.key] = `Enter a number, for example ${field.example ?? "8.1"}.`;
      } else if (parsed < 0) {
        found[field.key] = "Cannot be negative.";
      } else if (INTEGER_KEYS.has(field.key) && !Number.isInteger(parsed)) {
        found[field.key] = "Enter a whole number.";
      }
    }
  }
  return found;
}

function stepTitlesForKeys(steps: Step[], keys: string[]): string[] {
  const titles: string[] = [];
  for (const key of keys) {
    const title = steps.find((s) => s.fields.some((f) => f.key === key))?.title;
    if (title && !titles.includes(title)) titles.push(title);
  }
  return titles;
}

function friendlySaveError(error: unknown): string {
  const message = error instanceof Error ? error.message : "unknown error";
  return message === "Failed to fetch"
    ? "cannot reach the server — check that the backend is running, then try again"
    : message;
}

export default function OnboardingPage() {
  const [steps, setSteps] = useState<Step[]>([]);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const answersRef = useRef<Record<string, string>>({});
  const lastSavedRef = useRef<string>("");
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const router = useRouter();

  /** Keep the ref and the state in lockstep — autosave reads the ref. */
  function commitAnswers(next: Record<string, string>) {
    answersRef.current = next;
    setAnswers(next);
  }

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    []
  );

  useEffect(() => {
    // Guided setup schema (deep links: /onboarding?step=budget).
    fetch(`${API}/onboarding/schema`)
      .then((r) => r.json())
      .then((d: { steps?: Step[] }) => {
        const loaded = Array.isArray(d.steps) ? d.steps : [];
        setSteps(loaded);
        const wanted = new URLSearchParams(window.location.search).get("step");
        if (wanted) {
          const target = loaded.findIndex((s) => s.id === wanted);
          if (target >= 0) setIndex(target);
        }
      })
      .catch(() => setSteps([]));

    // Prefill from the saved profile so returning users never retype
    // answers they already gave.
    fetch(`${API}/me/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((p: Record<string, unknown> | null) => {
        if (!p) return;
        const next = { ...answersRef.current };
        for (const key of PREFILL_KEYS) {
          const v = p[key];
          const empty = v === null || v === undefined || v === "";
          if (!empty && !next[key]) next[key] = String(v);
        }
        commitAnswers(next);
      })
      .catch(() => {
        /* prefill is best-effort */
      });

    // Server-side completion: honest numbers, not a guess from the step index.
    getOnboardingProgress()
      .then(setProgress)
      .catch(() => {
        /* progress is a nicety; the wizard works without it */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function refreshProgress() {
    getOnboardingProgress()
      .then(setProgress)
      .catch(() => {});
  }

  async function performSave(flash: boolean): Promise<boolean> {
    const payload = { ...answersRef.current };
    const snapshot = JSON.stringify(payload);
    if (!flash && snapshot === lastSavedRef.current) return true; // nothing changed
    setSaving(true);
    setSaveError(null);
    try {
      await saveOnboardingAnswers(payload);
      lastSavedRef.current = snapshot;
      if (flash) {
        setSaved(true);
        if (flashTimer.current) clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setSaved(false), 2000);
      }
      refreshProgress();
      return true;
    } catch (e) {
      setSaveError(friendlySaveError(e));
      return false;
    } finally {
      setSaving(false);
    }
  }

  /** Saves run one at a time so an older write can never land last. */
  function save(flash = true): Promise<boolean> {
    const run = saveChainRef.current.then(() => performSave(flash));
    saveChainRef.current = run.then(
      () => undefined,
      () => undefined
    );
    return run;
  }

  function cancelDebounce() {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
  }

  /** Autosave on blur / step change: debounced, silent, skip when unchanged. */
  function saveNow() {
    cancelDebounce();
    void save(false);
  }

  function scheduleSave() {
    cancelDebounce();
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      void save(false);
    }, 800);
  }

  function onFieldChange(key: string, value: string) {
    commitAnswers({ ...answersRef.current, [key]: value });
    setErrors((prev) => (prev[key] ? { ...prev, [key]: "" } : prev));
    scheduleSave();
  }

  function goToStep(next: number) {
    saveNow();
    setErrors({});
    setIndex(next);
  }

  function goNext() {
    const step = steps[index];
    const found = validateStep(step, answersRef.current);
    setErrors(found);
    const firstBad = step.fields.find((f) => found[f.key]);
    if (firstBad) {
      document.getElementById(`field-${firstBad.key}`)?.focus();
      return;
    }
    goToStep(index + 1);
  }

  // Final step: always persist answers before leaving the flow.
  async function finish() {
    const found = validateStep(steps[index], answersRef.current);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    if (await save(true)) router.push("/research");
  }

  if (steps.length === 0) {
    return (
      <main className="mx-auto max-w-xl px-5 py-16">
        <LoadingNote what="Loading your guided setup…" />
      </main>
    );
  }

  const step = steps[index];
  const stepProgress = Math.round(((index + 1) / steps.length) * 100);
  const completion = Math.round(progress ? progress.completion_percent : stepProgress);
  const missingTitles = progress
    ? stepTitlesForKeys(steps, progress.missing_required_keys)
    : [];
  const answered = new Set(progress?.answered_keys ?? []);
  const isReview = step.fields.length === 0;

  function renderField(field: Field) {
    const value = answers[field.key] ?? "";
    const error = errors[field.key];
    const hintId = `hint-${field.key}`;
    const errorId = `error-${field.key}`;
    const describedBy = error ? `${hintId} ${errorId}` : hintId;
    const common = {
      id: `field-${field.key}`,
      className: "field",
      value,
      "aria-describedby": describedBy,
      "aria-invalid": error ? true : undefined,
      onBlur: saveNow,
    };
    return (
      <div key={field.key} className="flex flex-col gap-1.5">
        <label className="label" htmlFor={`field-${field.key}`}>
          {field.question}
          {field.required && (
            <span className="ml-1.5 text-xs font-normal text-ink-faint">(required)</span>
          )}
        </label>
        <span className="hint" id={hintId}>
          {field.explanation}
          {field.example ? ` e.g. ${field.example}` : ""}
          {field.input_type === "list" ? " — separate items with commas" : ""}
        </span>
        {field.why_we_ask && (
          <span className="text-xs italic text-ink-soft">Why we ask: {field.why_we_ask}</span>
        )}
        {field.input_type === "choice" ? (
          <select
            {...common}
            onChange={(e) => onFieldChange(field.key, e.target.value)}
          >
            <option value="">Skip for now</option>
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        ) : (
          <input
            {...common}
            inputMode={
              field.input_type === "number"
                ? INTEGER_KEYS.has(field.key)
                  ? "numeric"
                  : "decimal"
                : undefined
            }
            placeholder={field.example ?? undefined}
            onChange={(e) => onFieldChange(field.key, e.target.value)}
          />
        )}
        {error && (
          <span className="text-xs text-danger" id={errorId} role="alert">
            {error}
          </span>
        )}
      </div>
    );
  }

  function renderReview() {
    return (
      <div className="flex flex-col gap-4">
        {missingTitles.length > 0 && (
          <p
            role="status"
            className="rounded-lg border border-line bg-paper-dark px-4 py-3 text-sm text-ink-soft"
          >
            Still needed: {missingTitles.join(", ")} — you can finish now and fill these in
            later.
          </p>
        )}
        {steps
          .filter((s) => s.id !== "review" && s.fields.length > 0)
          .map((s) => (
            <section key={s.id} className="card p-4" aria-label={`${s.title} summary`}>
              <div className="mb-3 flex items-center justify-between gap-3 border-b border-line pb-2">
                <h2 className="label">{s.title}</h2>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => goToStep(steps.indexOf(s))}
                  aria-label={`Edit ${s.title} answers`}
                >
                  Edit
                </button>
              </div>
              <dl className="flex flex-col gap-1.5 text-sm">
                {s.fields.map((f) => {
                  const value = (answers[f.key] ?? "").trim();
                  const known = value !== "" || answered.has(f.key);
                  const term = REVIEW_TERMS[f.key];
                  const label = f.question.replace(/\?+$/, "");
                  return (
                    <div key={f.key} className="flex items-baseline justify-between gap-4">
                      <dt className="text-ink-faint">
                        {term ? <Term term={term}>{label}</Term> : label}
                      </dt>
                      <dd
                        className={
                          known
                            ? "text-right font-medium text-ink"
                            : "text-right text-ink-faint"
                        }
                      >
                        {value || (known ? "Saved ✓" : f.required ? "Still needed" : "Skipped")}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </section>
          ))}
      </div>
    );
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-10">
      {/* Progress: server-side completion + where you are in the wizard */}
      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <p className="eyebrow">
            Step {index + 1} of {steps.length}
          </p>
          <span className="text-xs tabular-nums text-ink-faint">{completion}% complete</span>
        </div>
        <div
          className="h-1 w-full overflow-hidden rounded bg-paper-dark"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={completion}
          aria-label="Setup completion"
        >
          <div className="h-full bg-forest transition-all" style={{ width: `${completion}%` }} />
        </div>
        <ol className="mt-3 flex flex-wrap gap-1.5" aria-label="Setup steps">
          {steps.map((s, i) => (
            <li key={s.id} aria-current={i === index ? "step" : undefined}>
              <span className={`chip ${i === index ? "chip-good" : "chip-neutral"}`}>
                {i + 1}. {s.title}
              </span>
            </li>
          ))}
        </ol>
        {missingTitles.length > 0 && (
          <p className="mt-2 text-xs text-ink-faint" role="status">
            Still needed: {missingTitles.join(", ")}
          </p>
        )}
      </div>

      <div className="border-b border-line pb-4">
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          {step.title}
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          Plain language throughout — leave anything unknown and it stays unknown.
        </p>
      </div>

      {isReview ? (
        renderReview()
      ) : (
        <div className="flex flex-col gap-4">
          {groupsFor(step).map((group) => (
            <section key={group.title} className="card p-4" aria-label={group.title}>
              <h2 className="label mb-3 border-b border-line pb-2">{group.title}</h2>
              <div className="flex flex-col gap-4">{group.fields.map(renderField)}</div>
            </section>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <button className="btn-secondary" onClick={() => void save(true)} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {index > 0 && (
          <button
            className="btn-ghost"
            onClick={() => goToStep(index - 1)}
            aria-label={`Back to ${steps[index - 1].title} step`}
          >
            ← Back
          </button>
        )}
        {index < steps.length - 1 ? (
          <button
            className="btn-primary ml-auto"
            onClick={goNext}
            aria-label={`Next: ${steps[index + 1].title} step`}
          >
            Next →
          </button>
        ) : (
          <button className="btn-primary ml-auto" onClick={() => void finish()} disabled={saving}>
            {saving ? "Saving…" : "Done — build my strategy →"}
          </button>
        )}
      </div>
      {saved && (
        <p role="status" className="text-sm text-forest">
          Saved.
        </p>
      )}
      {saveError && (
        <p role="alert" className="text-sm text-danger">
          Could not save: {saveError} — your answers are still here, try again.
        </p>
      )}
    </main>
  );
}
