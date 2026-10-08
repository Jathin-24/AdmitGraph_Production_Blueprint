import Link from "next/link";

const STEPS = [
  {
    n: "01",
    title: "Choose what you want to study",
    body: "Degree, field, countries — one plain-language question at a time. No jargon, no forms that look like visa paperwork.",
  },
  {
    n: "02",
    title: "Share your profile and budget",
    body: "Academics, language scores, experience, budget. Every field explains why we ask and what happens if you leave it unknown.",
  },
  {
    n: "03",
    title: "We research live evidence",
    body: "Real searches against real sources. Requirements, costs and deadlines are verified, scored against your profile, and traced back to where each claim came from.",
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
      {/* Hero */}
      <section className="flex flex-col items-center border-b border-line py-24 text-center">
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
        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Link href="/research" className="btn-primary px-6 py-2.5">
            Build my strategy
          </Link>
          <Link href="/onboarding" className="btn-secondary px-6 py-2.5">
            First time studying abroad? Start here
          </Link>
          <Link href="/research" className="btn-secondary px-6 py-2.5">
            See a full example →
          </Link>
        </div>
        <p className="mt-3 text-center text-xs text-ink-faint">
          Runs instantly with real sourced data — no search credits used.
        </p>
        <Link
          href="/dashboard"
          className="link mt-5 text-sm text-ink-soft"
        >
          Or open the strategy dashboard →
        </Link>
      </section>

      {/* How it works */}
      <section className="py-16" aria-label="How it works">
        <p className="eyebrow mb-8">How it works</p>
        <div className="grid gap-10 md:grid-cols-3">
          {STEPS.map((step) => (
            <div key={step.n} className="border-t-2 border-ink pt-4">
              <span className="display block text-3xl font-medium text-forest">
                {step.n}
              </span>
              <h2 className="display mt-3 text-lg font-medium leading-snug text-ink">
                {step.title}
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{step.body}</p>
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

      <footer className="border-t border-line py-8 text-xs text-ink-faint">
        AdmitGraph — evidence first. Search runs on our backend; your provider keys are
        never exposed to the browser.
      </footer>
    </main>
  );
}
