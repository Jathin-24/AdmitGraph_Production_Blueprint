"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
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
  optionLabels?: Record<string, string>;
  required?: boolean;
}
interface Step {
  id: string;
  title: string;
  fields: Field[];
}

/** Fields the guided setup collects beyond GET /onboarding/schema. The
 *  backend accepts them as payload-only keys (services/onboarding.py:
 *  ENGLISH_TEST_META_FIELDS + SUBJECT_FIELDS) — labels only, no new rules. */
interface CustomField extends Field {
  group: string;
}

const CUSTOM_FIELDS: Record<string, CustomField[]> = {
  tests: [
    {
      key: "english_test_type",
      group: "English test",
      question: "Which English test is this score for?",
      explanation: "Programs state their requirement per test — a TOEFL 100 is not an IELTS 7.0.",
      input_type: "choice",
      options: ["IELTS", "IELTS_ACADEMIC", "TOEFL", "PTE", "DET"],
      optionLabels: {
        IELTS_ACADEMIC: "IELTS Academic",
        DET: "DET (Duolingo English Test)",
      },
      why_we_ask: "Your score only means something next to the test it came from.",
    },
    {
      key: "english_test_date",
      group: "English test",
      question: "When did you take it?",
      explanation: "Leave blank if you haven't taken the test yet.",
      input_type: "date",
      why_we_ask: "Scores usually expire after two years, so the date decides whether yours still counts.",
    },
    {
      key: "english_expiry_date",
      group: "English test",
      question: "When does the score expire?",
      explanation: "Optional — fill it in if your report states an expiry date.",
      input_type: "date",
      why_we_ask: "An expired score cannot be used for a future application deadline.",
    },
  ],
  education: [
    {
      key: "subjects",
      group: "Subjects you have studied",
      question: "Which subjects did you study?",
      explanation: "Add your main subjects, with credits if you know them.",
      input_type: "subjects",
      why_we_ask: "Prerequisite checks compare your subjects against what each program expects.",
    },
  ],
};

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

interface SubjectRow {
  name: string;
  credits: string;
}

function groupsFor(step: Step): { title: string; fields: Field[] }[] {
  const spec = FIELD_GROUPS[step.id];
  const byKey = new Map(step.fields.map((f) => [f.key, f]));
  const groups: { title: string; fields: Field[] }[] = [];
  const used = new Set<string>();

  if (spec) {
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
  } else {
    groups.push({ title: step.title, fields: [...step.fields] });
  }

  // Payload-only fields (english test meta, subjects) join their card — or
  // open a new one when the step has no matching group.
  for (const custom of CUSTOM_FIELDS[step.id] ?? []) {
    const target = groups.find((g) => g.title === custom.group);
    if (target) target.fields.push(custom);
    else groups.push({ title: custom.group, fields: [custom] });
  }

  return groups.length > 0 ? groups : [{ title: step.title, fields: step.fields }];
}

/** Per-field rules: required must be present, numbers must parse. These are
 *  the same checks the backend applies on save (whole numbers for integer
 *  columns, finite decimals elsewhere) — nothing extra is invented. */
function rulesFor(field: Field) {
  return {
    validate: (raw: unknown): boolean | string => {
      const value = typeof raw === "string" ? raw.trim() : "";
      if (!value) {
        if (field.required) return "This one is required before you continue.";
        return true;
      }
      if (field.input_type === "date") {
        return /^\d{4}-\d{2}-\d{2}$/.test(value) || "Enter a date, for example 2026-09-01.";
      }
      if (field.input_type === "number") {
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) {
          return `Enter a number, for example ${field.example ?? "8.1"}.`;
        }
        if (parsed < 0) return "Cannot be negative.";
        if (INTEGER_KEYS.has(field.key) && !Number.isInteger(parsed)) {
          return "Enter a whole number.";
        }
      }
      return true;
    },
  };
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
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [subjects, setSubjects] = useState<SubjectRow[]>([]);
  const [subjectsDirty, setSubjectsDirty] = useState(false);
  const [subjectsError, setSubjectsError] = useState<string | null>(null);
  const answersRef = useRef<Record<string, string>>({});
  const lastSavedRef = useRef<string>("");
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hydratingRef = useRef(false);
  const router = useRouter();

  // React Hook Form owns the field values; answersRef mirrors them so the
  // autosave chain (debounced, serialized) reads a plain snapshot.
  const {
    register,
    trigger,
    reset,
    watch,
    formState: { errors },
  } = useForm<Record<string, string>>({
    defaultValues: {},
    mode: "onTouched",
  });

  // Live values -> answersRef, plus debounced autosave (§Profile wizard:
  // autosave every step).
  useEffect(() => {
    const subscription = watch((values) => {
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(values ?? {})) {
        if (typeof value === "string") next[key] = value;
        else if (value !== undefined && value !== null) next[key] = String(value);
      }
      answersRef.current = next;
      if (!hydratingRef.current) scheduleSave();
    });
    return () => subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

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
        const merged = { ...answersRef.current };
        for (const key of PREFILL_KEYS) {
          const v = p[key];
          const empty = v === null || v === undefined || v === "";
          if (!empty && !merged[key]) merged[key] = String(v);
        }
        hydratingRef.current = true;
        answersRef.current = merged;
        reset(merged);
        // The subscription fires during reset; skip its autosave tick.
        setTimeout(() => {
          hydratingRef.current = false;
        }, 0);
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

  /** Payload-only `subjects` key: structured rows, sent only when touched so
   *  an untouched wizard never wipes subjects saved earlier. */
  function subjectsPayload(): { name: string; credits?: string }[] {
    return subjects
      .map((row) => ({
        name: row.name.trim(),
        ...(row.credits.trim() ? { credits: row.credits.trim() } : {}),
      }))
      .filter((row) => row.name !== "");
  }

  function validateSubjects(): boolean {
    for (const row of subjects) {
      const credits = row.credits.trim();
      if (!row.name.trim() && credits) {
        setSubjectsError("Each subject needs a name — or remove the row.");
        return false;
      }
      if (row.name.trim() && credits) {
        const parsed = Number(credits);
        if (!Number.isFinite(parsed)) {
          setSubjectsError("Credits must be a number, for example 4.");
          return false;
        }
        if (parsed < 0) {
          setSubjectsError("Subject credits cannot be negative.");
          return false;
        }
      }
    }
    setSubjectsError(null);
    return true;
  }

  async function performSave(flash: boolean): Promise<boolean> {
    const payload: Record<string, unknown> = { ...answersRef.current };
    if (subjectsDirty) payload.subjects = subjectsPayload();
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

  function goToStep(next: number) {
    saveNow();
    setIndex(next);
  }

  async function validateCurrentStep(): Promise<boolean> {
    const step = steps[index];
    const keys = step.fields.map((f) => f.key);
    let ok = keys.length === 0 ? true : await trigger(keys);
    if (ok) ok = validateSubjects();
    if (!ok) {
      // focus the first field the backend would have rejected
      const firstBad = keys.find((key) => {
        const el = document.getElementById(`field-${key}`);
        return el ? el.getAttribute("aria-invalid") === "true" : false;
      });
      if (firstBad) document.getElementById(`field-${firstBad}`)?.focus();
      else document.getElementById("field-subjects")?.focus();
    }
    return ok;
  }

  function goNext() {
    void validateCurrentStep().then((ok) => {
      if (ok) goToStep(index + 1);
    });
  }

  // Final step: always persist answers before leaving the flow.
  async function finish() {
    if (!(await validateCurrentStep())) return;
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
  const answers = watch();

  function renderField(field: Field) {
    const error = errors[field.key];
    const hintId = `hint-${field.key}`;
    const errorId = `error-${field.key}`;
    const describedBy = error ? `${hintId} ${errorId}` : hintId;

    if (field.input_type === "subjects") {
      return renderSubjects();
    }

    const fieldRegister = register(field.key, rulesFor(field));
    const common = {
      id: `field-${field.key}`,
      className: "field",
      "aria-describedby": describedBy,
      "aria-invalid": error ? true : undefined,
      onBlur: (event: React.FocusEvent<HTMLInputElement | HTMLSelectElement>) => {
        fieldRegister.onBlur(event);
        saveNow();
      },
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
          <select {...common} {...fieldRegister}>
            <option value="">Skip for now</option>
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {field.optionLabels?.[option] ?? option}
              </option>
            ))}
          </select>
        ) : field.input_type === "date" ? (
          <input {...common} type="date" {...fieldRegister} />
        ) : (
          <input
            {...common}
            type={field.input_type === "number" ? "text" : undefined}
            inputMode={
              field.input_type === "number"
                ? INTEGER_KEYS.has(field.key)
                  ? "numeric"
                  : "decimal"
                : undefined
            }
            placeholder={field.example ?? undefined}
            {...fieldRegister}
          />
        )}
        {error && (
          <span className="text-xs text-danger" id={errorId} role="alert">
            {error.message}
          </span>
        )}
      </div>
    );
  }

  function renderSubjects() {
    return (
      <div className="flex flex-col gap-2">
        <span className="label" id="hint-subjects">
          Which subjects did you study?
        </span>
        <span className="hint" id="hint-subjects-detail">
          Add your main subjects, with credits if you know them.
        </span>
        <ul className="flex flex-col gap-2">
          {subjects.map((row, i) => (
            <li key={i} className="flex flex-wrap items-end gap-2">
              <label className="flex min-w-[10rem] flex-1 flex-col gap-1">
                <span className="hint">Subject {i + 1}</span>
                <input
                  id={i === 0 ? "field-subjects" : `field-subjects-${i}`}
                  className="field"
                  placeholder="e.g. Mathematics"
                  value={row.name}
                  aria-describedby="hint-subjects-detail"
                  onChange={(e) => {
                    setSubjectsDirty(true);
                    setSubjectsError(null);
                    setSubjects(subjects.map((r, idx) => (idx === i ? { ...r, name: e.target.value } : r)));
                  }}
                />
              </label>
              <label className="flex w-28 flex-col gap-1">
                <span className="hint">Credits</span>
                <input
                  className="field"
                  inputMode="decimal"
                  placeholder="4"
                  value={row.credits}
                  aria-describedby="hint-subjects-detail"
                  onChange={(e) => {
                    setSubjectsDirty(true);
                    setSubjectsError(null);
                    setSubjects(
                      subjects.map((r, idx) => (idx === i ? { ...r, credits: e.target.value } : r))
                    );
                  }}
                />
              </label>
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label={`Remove subject ${i + 1}`}
                onClick={() => {
                  setSubjectsDirty(true);
                  setSubjectsError(null);
                  setSubjects(subjects.filter((_, idx) => idx !== i));
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="btn-ghost btn-sm self-start"
          onClick={() => {
            setSubjectsDirty(true);
            setSubjectsError(null);
            setSubjects([...subjects, { name: "", credits: "" }]);
          }}
        >
          + Add subject
        </button>
        {subjectsError && (
          <span className="text-xs text-danger" role="alert">
            {subjectsError}
          </span>
        )}
      </div>
    );
  }

  function reviewValue(field: Field): string {
    if (field.key === "subjects") {
      return subjects
        .map((row) => row.name.trim())
        .filter(Boolean)
        .join(", ");
    }
    return (answers[field.key] ?? "").trim();
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
          .filter((s) => s.id !== "review" && (s.fields.length > 0 || CUSTOM_FIELDS[s.id]))
          .map((s) => {
            const fields = [...s.fields, ...(CUSTOM_FIELDS[s.id] ?? [])];
            return (
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
                  {fields.map((f) => {
                    const value = reviewValue(f);
                    const known =
                      value !== "" ||
                      answered.has(f.key) ||
                      (f.key === "subjects" && subjects.some((row) => row.name.trim()));
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
            );
          })}
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
