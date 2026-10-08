"use client";

import Link from "next/link";

/**
 * "Where you are" guide strip (FRONTEND_SPEC: progressive disclosure).
 *
 * A four-step stepper that answers two questions at the top of the strategy
 * dashboard: where am I in the journey, and what comes next. Completion is
 * derived only from real fetched state passed in by the page — never guessed:
 *
 *  1. Profile       — done when onboarding progress reports 100 %
 *  2. Research      — done when a research run produced a strategy
 *  3. Your plan     — done when the selected strategy has portfolio rows
 *  4. Keep it fresh — ongoing monitoring, never a "finish line"
 *
 * Every step links to its page. Done steps show a ✓ plus screen-reader text,
 * and the current step carries a visible "Now" chip and aria-current="step" —
 * meaning is never conveyed by color alone (FRONTEND_SPEC §Accessibility).
 */

export interface GuideProfileState {
  completionPercent: number;
  missingRequired: number;
}

const STEPS = [
  { num: 1, title: "Profile", href: "/onboarding" },
  { num: 2, title: "Research", href: "/research" },
  { num: 3, title: "Your plan", href: "/dashboard" },
  { num: 4, title: "Keep it fresh", href: "/monitor" },
] as const;

export function GuideStrip({
  profile,
  researchReady,
  researchLoaded,
  planReady,
}: {
  /** Onboarding progress, or null while it is still loading / unavailable. */
  profile: GuideProfileState | null;
  /** A research run produced a strategy — from the page's strategies query. */
  researchReady: boolean;
  /** The strategies query has settled (success or error). */
  researchLoaded: boolean;
  /** The selected strategy already has portfolio rows to look at. */
  planReady: boolean;
}) {
  const done = [
    profile !== null && profile.completionPercent >= 100,
    researchReady,
    planReady,
    false, // "Keep it fresh" is an ongoing activity, not a finish line.
  ];
  const current = done.findIndex((isDone) => !isDone);

  function sublabel(index: number): string {
    if (index === 0) {
      if (!profile) return "Loading…";
      if (done[0]) return "Complete";
      return profile.missingRequired > 0
        ? `${profile.completionPercent}% complete · ${profile.missingRequired} required question${
            profile.missingRequired === 1 ? "" : "s"
          } left`
        : `${profile.completionPercent}% complete`;
    }
    if (index === 1) {
      if (!researchLoaded) return "Loading…";
      if (done[1]) return "Research run finished";
      return "We check live sources and build your shortlist";
    }
    if (index === 2) {
      return done[2] ? "Your shortlist is ready" : "Portfolio, risks and deadlines";
    }
    return current === 3 ? "Ongoing — requirements can change" : "After your plan is ready";
  }

  return (
    <nav className="card p-4" aria-label="Getting started">
      <p className="eyebrow mb-3">Where you are</p>
      <ol className="flex flex-col gap-2 sm:flex-row">
        {STEPS.map((step, index) => {
          const isDone = done[index];
          const isCurrent = index === current;
          return (
            <li key={step.href} className="sm:flex-1">
              <Link
                href={step.href}
                aria-current={isCurrent ? "step" : undefined}
                className={`flex h-full items-start gap-3 rounded-md border p-3 transition-colors hover:border-forest/60 ${
                  isCurrent ? "border-forest bg-white" : "border-line bg-paper/40"
                } ${isDone || isCurrent ? "text-ink" : "text-ink-faint"}`}
              >
                <span
                  aria-hidden
                  className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                    isDone
                      ? "bg-forest-tint text-forest"
                      : isCurrent
                        ? "bg-forest text-white"
                        : "bg-paper-dark text-ink-faint"
                  }`}
                >
                  {isDone ? "✓" : step.num}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    <span className="sr-only">
                      Step {step.num}: {step.title} —{" "}
                      {isDone ? "done" : isCurrent ? "current step" : "not started"}.
                    </span>
                    <span aria-hidden>{step.title}</span>
                    {isCurrent && <span className="chip chip-good">Now</span>}
                  </span>
                  <span className="mt-0.5 block text-xs text-ink-faint">{sublabel(index)}</span>
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export default GuideStrip;
