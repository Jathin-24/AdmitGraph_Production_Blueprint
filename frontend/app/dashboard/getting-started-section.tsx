"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo } from "react";
import { GuideStrip } from "../components/guide-strip";
import { getCompletion, getOnboardingProgress, getStrategies } from "../lib/api";
import { dueCopy, type StrategiesQuery, type StrategyQuery } from "./sections-shared";

type StrategyItem = Awaited<ReturnType<typeof getStrategies>>["items"][number];

/** One urgent action: a roadmap due date or portfolio deadline inside 30 days. */
type UrgentItem = { key: string; title: string; days: number; href?: string };


/** FRONTEND_SPEC §Beginner mode — the first-run card's three plain steps. */
const FIRST_RUN_STEPS = [
  {
    title: "Answer a few questions",
    body: "What you want to study, where, and your budget — one decision per screen.",
  },
  {
    title: "We research real sources",
    body: "Live requirements, costs and deadlines, checked against your profile.",
  },
  {
    title: "Get your shortlist",
    body: "Programs with fit scores, costs, deadlines and the risks that matter.",
  },
];


export function GettingStartedSection({
  strategies,
  items,
  urgent,
  detail,
}: {
  strategies: StrategiesQuery;
  items: StrategyItem[];
  urgent: UrgentItem[];
  detail: StrategyQuery;
}) {

  const completion = useQuery({ queryKey: ["completion"], queryFn: getCompletion });
  // Onboarding wizard progress — drives the guide strip's profile step and the
  // "Finish your profile" priority in "Do this next". Short staleTime so the
  // strip stays fresh without hammering the endpoint.
  const onboardingProgress = useQuery({
    queryKey: ["onboarding-progress"],
    queryFn: getOnboardingProgress,
    staleTime: 30_000,
  });

  const missingFields = completion.data?.missing_fields ?? [];
  const profileIncomplete =
    (completion.data?.profile_completion ?? 100) < 100 && missingFields.length > 0;

  /* --------------------------------------------------- guided next step */
  // "Do this next" priority (FRONTEND_SPEC: the dashboard must always say
  // what to do next): incomplete profile > no strategy yet > the most
  // pressing urgent item > review the plan. Real fetched state only.
  const dataReady =
    (strategies.isSuccess || strategies.isError) &&
    (onboardingProgress.isSuccess || onboardingProgress.isError);

  const nextStep = useMemo(() => {
    const progress = onboardingProgress.data;
    if (progress && progress.missing_required_keys.length > 0) {
      const answered = progress.answered_keys.length;
      const total = answered + progress.missing_required_keys.length;
      return {
        title: "Finish your profile",
        body: `${answered} of ${total} questions answered — research scores you against a complete picture.`,
        cta: "Finish my profile",
        href: "/onboarding",
      };
    }
    if (strategies.isSuccess && items.length === 0) {
      return {
        title: "Run your first research",
        body: "We check live requirements, costs and deadlines, then build your shortlist.",
        cta: "Run my first research",
        href: "/research",
      };
    }
    if (strategies.isSuccess && urgent.length > 0) {
      const top = urgent.reduce((best, u) => (u.days < best.days ? u : best), urgent[0]);
      return {
        title: top.title,
        body: `This one is ${dueCopy(top.days)}.`,
        cta: top.href ? "Open" : "See next tasks",
        href: top.href ?? "#next-tasks",
      };
    }
    if (strategies.isSuccess) {
      return {
        title: "Review your plan",
        body: "See what could get in the way and what to do about each risk.",
        cta: "Review my plan",
        href: "#risks",
      };
    }
    return null;
  }, [onboardingProgress.data, strategies.isSuccess, items, urgent]);


  return (
    <>

      {/* Where you are — the guided journey strip. */}
      <GuideStrip
        profile={
          onboardingProgress.data
            ? {
                completionPercent: onboardingProgress.data.completion_percent,
                missingRequired: onboardingProgress.data.missing_required_keys.length,
              }
            : null
        }
        researchReady={strategies.isSuccess && items.length > 0}
        researchLoaded={strategies.isSuccess || strategies.isError}
        planReady={!!detail.data && detail.data.portfolio.length > 0}
      />

      {/* Do this next — ONE card, one primary CTA, derived from real state. */}
      {dataReady && nextStep && (
        <section
          className="card flex flex-wrap items-center gap-4 border-forest/40 p-5"
          aria-label="Do this next"
        >
          <div className="min-w-0 flex-1">
            <p className="eyebrow mb-1">Do this next</p>
            <p className="display text-xl font-medium text-ink">{nextStep.title}</p>
            <p className="mt-1 text-sm text-ink-soft">{nextStep.body}</p>
          </div>
          <Link href={nextStep.href} className="btn-primary shrink-0">
            {nextStep.cta}
          </Link>
        </section>
      )}

      {strategies.isSuccess && items.length === 0 && (
        <section className="card p-6" aria-label="Getting started">
          <h2 className="display text-xl font-medium text-ink">Your plan doesn&apos;t exist yet</h2>
          <p className="mt-1 text-sm text-ink-soft">Three steps to a shortlist you can trust:</p>
          <ol className="mt-4 grid gap-3 sm:grid-cols-3">
            {FIRST_RUN_STEPS.map((step, index) => (
              <li key={step.title} className="rounded-lg border border-line bg-paper/50 p-4">
                <span aria-hidden className="display text-lg font-medium text-forest">
                  {index + 1}
                </span>
                <p className="mt-1 text-sm font-medium text-ink">{step.title}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">{step.body}</p>
              </li>
            ))}
          </ol>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Link href="/onboarding" className="btn-primary">
              Start with my profile
            </Link>
            <Link href="/research" className="btn-secondary">
              See a live example first
            </Link>
          </div>
          {profileIncomplete && (
            <p className="mt-3 text-sm text-ink-soft">
              Some profile answers are still missing ({missingFields.slice(0, 3).join(", ")}).
              Finish the{" "}
              <Link href="/onboarding" className="link">
                guided setup →
              </Link>{" "}
              and research will score against a complete picture.
            </p>
          )}
        </section>
      )}

    </>
  );
}
