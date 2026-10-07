"use client";

import { useEffect, useState } from "react";

interface Field {
  key: string;
  question: string;
  explanation: string;
  example?: string;
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

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"}/onboarding/schema`)
      .then((r) => r.json())
      .then((d) => setSteps(d.steps))
      .catch(() => setSteps([]));
  }, []);

  if (steps.length === 0) return <main className="p-10">Loading your guided setup…</main>;

  const step = steps[index];

  async function save() {
    await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"}/onboarding/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-10">
      <h1 className="text-2xl font-semibold">{step.title}</h1>
      <p className="text-neutral-500">Step {index + 1} of {steps.length}</p>
      {step.fields.map((f) => (
        <label key={f.key} className="flex flex-col gap-1">
          <span className="font-medium">{f.question}</span>
          <span className="text-sm text-neutral-500">{f.explanation}{f.example ? ` e.g. ${f.example}` : ""}</span>
          <input
            className="rounded border p-2"
            value={answers[f.key] ?? ""}
            onChange={(e) => setAnswers({ ...answers, [f.key]: e.target.value })}
          />
        </label>
      ))}
      <div className="flex gap-3">
        <button className="rounded border px-4 py-2" onClick={save}>Save</button>
        {index > 0 && <button className="rounded border px-4 py-2" onClick={() => setIndex(index - 1)}>Back</button>}
        {index < steps.length - 1 && <button className="rounded bg-black px-4 py-2 text-white" onClick={() => setIndex(index + 1)}>Next</button>}
      </div>
      {saved && <p role="status">Saved.</p>}
    </main>
  );
}
