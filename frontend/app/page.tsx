import Link from "next/link";

const STEPS = [
  {
    title: "1. Choose what you want to study",
    body: "Tell us the degree, field and countries you care about — one question at a time, plain language throughout.",
  },
  {
    title: "2. Share your profile and budget",
    body: "Academics, language scores, experience and budget. Every field explains why we ask and what happens if it's unknown.",
  },
  {
    title: "3. We research live evidence and build your plan",
    body: "SerpApi searches run on our backend, requirements are verified against real sources, and you get a portfolio, risks and a roadmap — each claim traceable to its evidence.",
  },
];

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 p-8 text-center">
      <section className="flex flex-col items-center gap-6">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight">
          Study abroad with a plan, not a pile of tabs.
        </h1>
        <p className="max-w-xl text-lg text-neutral-600">
          AdmitGraph checks live requirements, costs, deadlines and risks against your profile —
          then tells you what to do next.
        </p>
        <div className="flex flex-wrap justify-center gap-4">
          <Link href="/research" className="rounded-full bg-black px-6 py-3 text-white">
            Build my strategy
          </Link>
          <Link href="/onboarding" className="rounded-full border px-6 py-3">
            First time studying abroad? Start here
          </Link>
          <Link href="/dashboard" className="rounded-full border px-6 py-3">
            Strategy dashboard
          </Link>
        </div>
      </section>

      <section
        className="grid w-full max-w-3xl gap-4 text-left sm:grid-cols-3"
        aria-label="How it works"
      >
        {STEPS.map((step) => (
          <div key={step.title} className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
            <h2 className="mb-1 text-sm font-semibold">{step.title}</h2>
            <p className="text-sm leading-relaxed text-neutral-600">{step.body}</p>
          </div>
        ))}
      </section>

      <p className="max-w-xl text-xs text-neutral-400">
        No admission predictions, no invented facts. UNKNOWN stays UNKNOWN until real evidence says
        otherwise — every claim shows its source, timestamp and confidence.
      </p>
    </main>
  );
}
