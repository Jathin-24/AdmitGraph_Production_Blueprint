import type { Metadata } from "next";
import Link from "next/link";

/** P2-20 per-route metadata. The landing page is a server component (only
 *  Links — no client interactivity), so this exports statically and the
 *  description below is what search engines and link previews show. */
export const metadata: Metadata = {
  title: "Evidence-based study-abroad planning",
  description:
    "AdmitGraph checks live requirements, costs, deadlines and risks against your profile — then tells you what to do next. Fit, never admission probabilities; every claim sourced.",
};

const STEPS = [
  {
    n: "01",
    title: "Answer a few plain questions",
    body: "Your background, grades, English score, budget and where you want to study — one short step at a time. Skip anything you don't know yet; it stays marked as unknown, never guessed.",
  },
  {
    n: "02",
    title: "We go research real sources",
    body: "Live searches against real university and authority sites. Requirements, fees and deadlines are verified and traced back to where each fact came from.",
  },
  {
    n: "03",
    title: "Get a plan you can act on",
    body: "A shortlist with the reason each program fits you, the risks that could hold an application back, and one clear next step.",
  },
];

/** What lands at the end of the guided setup — real product surfaces only. */
const OUTCOMES = [
  {
    title: "A shortlist that fits you",
    body: "Reach, target and lower-risk programs, each with a fit score, the top reasons it matches you, and the next deadline to watch.",
  },
  {
    title: "Real numbers, with sources",
    body: "Tuition, living costs, requirements and deadlines — checked against live sources, each claim traced back to where it came from.",
  },
  {
    title: "Risks before they surprise you",
    body: "What could hold an application back, how serious it is, and what to do next — while you still have time to act.",
  },
];

const PRINCIPLES = [
  ["No admission probabilities.", "We compute fit — never predict your chances."],
  ["UNKNOWN stays UNKNOWN.", "Missing evidence is shown as missing, never filled in."],
  ["Every claim is sourced.", "Source, timestamp, confidence, freshness — on all of them."],
];

export default function Home() {
  return (
    <main className="mx-auto max-w-5xl px-5">
      {/* Hero — exactly two paths: build now, or take the guided first step */}
      <section
        className="flex flex-col items-center border-b border-line py-24 text-center"
        aria-label="Introduction"
      >
        <p className="eyebrow mb-5">Evidence-based study-abroad planning</p>
        <h1 className="display max-w-3xl text-[clamp(2.4rem,5.5vw,3.6rem)] font-medium leading-[1.06] text-ink">
          Study abroad with a plan,
          <br />
          not a pile of tabs.
        </h1>
        <p className="lede mt-6 max-w-xl">
          AdmitGraph checks live requirements, costs, deadlines and risks against your
          profile — then tells you what to do next.
        </p>
        {/* The newcomer path IS the primary path — a first-time student should
            never have to choose between three equally-weighted buttons. */}
        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Link href="/onboarding" className="btn-primary px-6 py-2.5">
            Start my free plan
          </Link>
          <Link href="/research" className="btn-secondary px-6 py-2.5">
            See a live example first
          </Link>
        </div>
        <p className="mt-4 text-xs text-ink-faint">
          No sign-up needed to look around — the example runs instantly with real sourced
          data, no search credits used.
        </p>
      </section>

      {/* How it works — the 3-step beginner intro */}
      <section className="py-16" aria-label="How it works">
        <h2 className="eyebrow mb-8">How it works</h2>
        <div className="grid gap-10 md:grid-cols-3">
          {STEPS.map((step) => (
            <div key={step.n} className="border-t-2 border-ink pt-4">
              <span className="display block text-3xl font-medium text-forest">
                {step.n}
              </span>
              <h3 className="display mt-3 text-lg font-medium leading-snug text-ink">
                {step.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{step.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What you get — outcomes in plain student language */}
      <section className="border-t border-line py-16" aria-label="What you get">
        <h2 className="eyebrow mb-8">What you get</h2>
        <div className="grid gap-6 md:grid-cols-3">
          {OUTCOMES.map((outcome) => (
            <div key={outcome.title} className="card p-5">
              <h3 className="display text-base font-medium text-ink">{outcome.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{outcome.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Principles strip */}
      <section className="border-t border-line py-10" aria-label="Our principles">
        <div className="grid gap-6 md:grid-cols-3">
          {PRINCIPLES.map(([headline, body]) => (
            <div key={headline}>
              <p className="display text-base font-medium text-ink">{headline}</p>
              <p className="mt-1 text-sm text-ink-faint">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Returning users — out of the hero's primary path */}
      <p className="border-t border-line py-8 text-center text-sm text-ink-faint">
        Already have a plan?{" "}
        <Link href="/dashboard" className="link">
          Open My Plan →
        </Link>
      </p>

      <footer className="border-t border-line py-8 text-xs text-ink-faint">
        AdmitGraph — evidence first. Search runs on our backend; your provider keys are
        never exposed to the browser.
      </footer>
    </main>
  );
}
