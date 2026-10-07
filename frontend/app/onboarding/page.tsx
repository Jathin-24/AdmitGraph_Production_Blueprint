"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LoadingNote } from "../components/ui";

interface Field {
  key: string;
  question: string;
  explanation: string;
  example?: string;
  why_we_ask?: string;
  input_type: string;
}
interface Step {
  id: string;
  title: string;
  fields: Field[];
}

export default function OnboardingPage() {
  const [steps, setSteps] = useState<Step[]>([]);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    []
  );

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"}/onboarding/schema`)
      .then((r) => r.json())
      .then((d) => setSteps(d.steps))
      .catch(() => setSteps([]));
  }, []);

  if (steps.length === 0) {
    return (
      <main className="mx-auto max-w-xl px-5 py-16">
        <LoadingNote what="Loading your guided setup…" />
      </main>
    );
  }

  const step = steps[index];
  const progress = Math.round(((index + 1) / steps.length) * 100);

  async function save() {
    await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"}/onboarding/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    setSaved(true);
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaved(false), 2000);
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-10">
      {/* Progress */}
      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <p className="eyebrow">
            Step {index + 1} of {steps.length}
          </p>
          <span className="text-xs tabular-nums text-ink-faint">{progress}%</span>
        </div>
        <div className="h-1 w-full overflow-hidden rounded bg-paper-dark">
          <div className="h-full bg-forest transition-all" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <div className="border-b border-line pb-4">
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          {step.title}
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          Plain language throughout — leave anything unknown and it stays unknown.
        </p>
      </div>

      <div className="flex flex-col gap-5">
        {step.fields.map((f) => (
          <label key={f.key} className="flex flex-col gap-1.5">
            <span className="label">{f.question}</span>
            <span className="hint">
              {f.explanation}
              {f.example ? ` e.g. ${f.example}` : ""}
            </span>
            {f.why_we_ask && (
              <span className="text-xs italic text-ink-soft">Why we ask: {f.why_we_ask}</span>
            )}
            <input
              className="field"
              value={answers[f.key] ?? ""}
              onChange={(e) => setAnswers({ ...answers, [f.key]: e.target.value })}
            />
          </label>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <button className="btn-secondary" onClick={save}>
          Save
        </button>
        {index > 0 && (
          <button className="btn-ghost" onClick={() => setIndex(index - 1)}>
            ← Back
          </button>
        )}
        {index < steps.length - 1 ? (
          <button className="btn-primary ml-auto" onClick={() => setIndex(index + 1)}>
            Next →
          </button>
        ) : (
          <Link href="/research" className="btn-primary ml-auto">
            Done — build my strategy →
          </Link>
        )}
      </div>
      {saved && (
        <p role="status" className="text-sm text-forest">
          Saved.
        </p>
      )}
    </main>
  );
}
