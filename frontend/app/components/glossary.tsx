"use client";

/**
 * Education-first glossary (MASTER_SPEC §5): plain-language definitions for
 * the terms onboarding and the profile keep using, shown on hover *and*
 * keyboard focus. General education knowledge only — never a fact about a
 * specific university.
 *
 * Usage: <Term term="intake">Winter 2027</Term>
 */

import { useEffect, useId, useRef, useState } from "react";

export interface GlossaryEntry {
  term: string;
  definition: string;
  why: string;
}

export const GLOSSARY = {
  masters_degree: {
    term: "Master's degree",
    definition:
      "A postgraduate degree you take after a bachelor's — usually one year in Europe, two in the US. Most programs expect a bachelor's in a related field first.",
    why: "It decides which of your previous degrees count as 'related'.",
  },
  intake: {
    term: "Intake",
    definition:
      "The term a program starts admitting students, like Winter 2027 or Summer 2027. Each intake has its own application deadline.",
    why: "Pick your intake first and every deadline falls into place behind it.",
  },
  prerequisite: {
    term: "Prerequisite",
    definition:
      "Something you must already have before a program will consider you — a subject, a credit count, or a related bachelor's degree.",
    why: "Failing a prerequisite is an early rejection, whatever your grades are.",
  },
  ects: {
    term: "ECTS / credits",
    definition:
      "Credits measure workload: one ECTS credit is roughly 25-30 hours. Degrees and courses are priced in credits, and some programs require a matching credit count from your previous degree.",
    why: "Credit mismatches are one of the most common hidden eligibility problems.",
  },
  language_requirement: {
    term: "Language requirement",
    definition:
      "The minimum score an English test (IELTS, TOEFL, PTE) must show before a program accepts your application. Scores usually expire after two years.",
    why: "A missing or expired score blocks submission even with strong grades.",
  },
  application_deadline: {
    term: "Application deadline",
    definition:
      "The last date a program accepts applications for an intake. Most programs will not review anything that arrives after it.",
    why: "Missing a deadline usually means waiting months for the next intake.",
  },
  tuition_living: {
    term: "Tuition vs living costs",
    definition:
      "Tuition is what the university charges; living costs are rent, food, transport and insurance in the city you study in.",
    why: "Living costs often rival tuition — budgeting only tuition is a common surprise.",
  },
  scholarship: {
    term: "Scholarship",
    definition:
      "Money that reduces what you pay, usually tuition. It rarely covers everything, and an offer you have not received yet is not funding yet.",
    why: "Planning on a scholarship you don't have turns a hope into a funding gap.",
  },
  visa_financial_proof: {
    term: "Visa / financial proof",
    definition:
      "Most student visas ask you to prove you can fund your first year — a bank balance, a sanctioned loan, or a scholarship letter.",
    why: "The proof is a document you need weeks before you can travel.",
  },
  cgpa: {
    term: "CGPA / percentage",
    definition:
      "Your cumulative grade average: CGPA (like 8.1 out of 10) and percentage (like 78%) are two ways of reporting the same result.",
    why: "Academic minimums are checked against whichever number your transcript prints.",
  },
  profile_completion: {
    term: "Profile completion",
    definition:
      "The share of the facts your recommendations are scored against that you have actually told us.",
    why: "More completed answers means fewer unknowns and sharper comparisons.",
  },
} as const satisfies Record<string, GlossaryEntry>;

export type GlossaryKey = keyof typeof GLOSSARY;

export function Term({
  term,
  children,
}: {
  term: GlossaryKey;
  children: React.ReactNode;
}) {
  const entry: GlossaryEntry | undefined = GLOSSARY[term];
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const id = useId();

  // Click outside closes (keyboard users get Escape and blur instead).
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  if (!entry) return <>{children}</>;

  const closeIfBlurred = (event: React.FocusEvent<HTMLButtonElement>) => {
    if (!wrapRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
  };
  const closeIfUnfocused = () => {
    if (!wrapRef.current?.contains(document.activeElement)) setOpen(false);
  };

  return (
    <span ref={wrapRef} className="relative inline-block">
      <button
        type="button"
        className="rounded-sm underline decoration-dotted underline-offset-4 transition-colors hover:text-forest focus-visible:text-forest"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-describedby={open ? id : undefined}
        aria-label={`What is ${entry.term}?`}
        onClick={() => setOpen((value) => !value)}
        onFocus={() => setOpen(true)}
        onBlur={closeIfBlurred}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={closeIfUnfocused}
      >
        {children}
        <span aria-hidden className="ml-0.5 font-medium text-forest">
          ?
        </span>
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-0 top-full z-30 mt-1 block w-64 rounded-lg border border-line bg-white p-3 text-left text-xs leading-relaxed text-ink-soft shadow-card"
        >
          <span className="block font-medium text-ink">{entry.term}</span>
          <span className="mt-0.5 block">{entry.definition}</span>
          <span className="mt-1.5 block italic text-ink-faint">
            Why this matters: {entry.why}
          </span>
        </span>
      )}
    </span>
  );
}
