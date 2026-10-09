"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader, Section } from "../components/ui";
import {
  COUNTRIES,
  LAST_CHECKED,
  SCHOLARSHIPS_HREF,
  VERIFY_FIGURES_NOTE,
  type CountryCode,
  type OfficialSource,
} from "./data";
import {
  clearCheckedDocs,
  completionPercent,
  loadCheckedDocs,
  saveCheckedDocs,
  toggleChecked,
  visaDocsStorageKey,
} from "./storage";

/**
 * W14 — /visa: destination-country selector, typical visa flow, financial
 * proof expectations, a per-country document checklist persisted to
 * localStorage, and funding tips that cross-link to /scholarships.
 *
 * Static content only: no API calls, no backend. Every section links to a
 * real official source (resolved when data.ts was written — see
 * LAST_CHECKED), and the banner up top tells students to verify there.
 */

/** Official-source footer line rendered at the end of every section.
 *  Duplicate URLs (sections often cite the same authority twice) collapse
 *  into one link so React keys stay unique. */
function SourceLinks({ sources }: { sources: OfficialSource[] }) {
  const seen = new Set<string>();
  const unique = sources.filter((source) => {
    if (seen.has(source.url)) return false;
    seen.add(source.url);
    return true;
  });
  return (
    <p className="mt-4 border-t border-line pt-3 text-xs text-ink-faint">
      Official source{unique.length > 1 ? "s" : ""}:{" "}
      {unique.map((source, index) => (
        <span key={source.url}>
          {index > 0 && " · "}
          <a href={source.url} target="_blank" rel="noreferrer" className="link">
            {source.label}
          </a>
        </span>
      ))}
      {" — "}verify at the source: requirements change without notice.
    </p>
  );
}

export default function VisaPage() {
  const [countryCode, setCountryCode] = useState<CountryCode>("DE");
  const [checked, setChecked] = useState<string[]>([]);

  const country = COUNTRIES.find((c) => c.code === countryCode) ?? COUNTRIES[0];

  // Load this country's saved ticks after mount — SSR never reads storage.
  useEffect(() => {
    setChecked(loadCheckedDocs(countryCode));
  }, [countryCode]);

  function handleToggle(docId: string): void {
    setChecked((prev) => {
      const next = toggleChecked(prev, docId);
      saveCheckedDocs(countryCode, next);
      return next;
    });
  }

  function handleClear(): void {
    clearCheckedDocs(countryCode);
    setChecked([]);
  }

  const pct = completionPercent(checked, country.documents.length);

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Visa & funding"
        title="Your visa checklist."
        lede="A source-linked planning checklist for student residence visas in six common destinations: the typical steps, how financial proof actually works, the documents to gather — with your progress saved in this browser."
      />

      {/* Disclaimer banner — requirements must be verified at the source */}
      <div
        role="note"
        aria-label="Verify at the official source"
        className="rounded-lg border border-amberx/40 bg-amberx-tint px-4 py-3 text-sm text-ink-soft"
      >
        <p>
          Requirements, fees and figures change without notice — treat this page as a planning
          checklist, not legal advice. Every section below links to the official authority that
          decides applications; verify anything you will rely on directly at that source. Only the
          immigration authority can grant a visa — nothing here is a guarantee of approval.
        </p>
        <p className="mt-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
          Source links last checked: {LAST_CHECKED}
        </p>
      </div>

      {/* Country selector */}
      <section aria-label="Destination country">
        <h2 className="display mb-3 text-lg font-medium">Where are you going?</h2>
        <div role="group" aria-label="Destination country" className="flex flex-wrap gap-2">
          {COUNTRIES.map((c) => (
            <button
              key={c.code}
              type="button"
              aria-pressed={c.code === countryCode}
              onClick={() => setCountryCode(c.code)}
              className={c.code === countryCode ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            >
              {c.name}
            </button>
          ))}
        </div>
        <p className="mt-3 text-sm text-ink-soft">
          Visa category: <span className="font-medium text-ink">{country.visaName}</span>
        </p>
      </section>

      {/* 01 — typical visa steps (numbered flow) */}
      <Section index="01" title={`Typical steps — ${country.name}`}>
        <ol className="relative flex flex-col gap-4 border-l border-line pl-6">
          {country.steps.map((step, index) => (
            <li key={step} className="relative">
              <span
                aria-hidden
                className="absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border border-line-dark bg-white text-[11px] text-ink-soft"
              >
                {index + 1}
              </span>
              <p className="text-sm text-ink">{step}</p>
            </li>
          ))}
        </ol>
        <SourceLinks sources={country.stepsSources} />
      </Section>

      {/* 02 — financial proof expectations (forms described honestly) */}
      <Section index="02" title="Financial proof — what the form looks like">
        <p className="text-sm text-ink">{country.financialProof.intro}</p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-ink-soft">
          {country.financialProof.forms.map((form) => (
            <li key={form}>{form}</li>
          ))}
        </ul>
        {country.financialProof.figure && (
          <div
            role="note"
            aria-label="Figure quoted from the official source"
            className="mt-4 rounded-md border border-amberx/40 bg-amberx-tint px-3 py-2.5"
          >
            <p className="text-sm font-semibold text-ink">
              {country.financialProof.figure.amount}
            </p>
            <p className="mt-1 text-xs text-ink-soft">
              Read at{" "}
              <a
                href={country.financialProof.figure.source.url}
                target="_blank"
                rel="noreferrer"
                className="link"
              >
                {country.financialProof.figure.source.label}
              </a>
            </p>
            <p className="mt-1 text-xs font-medium text-amberx">{VERIFY_FIGURES_NOTE}</p>
          </div>
        )}
        <SourceLinks sources={country.financialSources} />
      </Section>

      {/* 03 — interactive documents checklist (localStorage per country) */}
      <Section
        index="03"
        title="Documents checklist"
        aside={
          <span className={pct === 100 ? "chip chip-good" : "chip chip-neutral"}>
            {pct}% ready
          </span>
        }
      >
        <p className="text-sm text-ink">
          Tick what you already have. This is a typical set — the mission or authority checklist
          for your case is the binding one, so compare against the official sources below.
        </p>
        <ul className="mt-3 flex flex-col gap-2">
          {country.documents.map((doc) => (
            <li key={doc.id}>
              <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-line bg-paper/50 px-3 py-2 transition-colors hover:bg-paper-dark">
                <input
                  type="checkbox"
                  checked={checked.includes(doc.id)}
                  onChange={() => handleToggle(doc.id)}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[#1d5c46]"
                />
                <span className="text-sm text-ink">
                  {doc.label}
                  {doc.note && <span className="block text-xs text-ink-faint">{doc.note}</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p role="status" className="text-sm text-ink-soft">
            {checked.length} of {country.documents.length} checked · {pct}% complete
          </p>
          <button type="button" onClick={handleClear} className="btn-ghost btn-sm">
            Clear ticks for {country.name}
          </button>
        </div>
        <p className="hint mt-2">
          Ticks are saved in this browser only, separately per country (stored under{" "}
          <code className="rounded bg-paper-dark px-1 py-0.5 font-mono text-[11px]">
            {visaDocsStorageKey(countryCode)}
          </code>
          ).
        </p>
        <SourceLinks sources={country.documentSources} />
      </Section>

      {/* 04 — funding tips, cross-linking to the scholarship finder */}
      <Section index="04" title="Funding tips">
        <ul className="list-disc space-y-2 pl-5 text-sm text-ink-soft">
          {country.fundingTips.map((tip) => (
            <li key={tip.text}>
              {tip.text}{" "}
              {tip.source && (
                <a
                  href={tip.source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="link text-xs"
                >
                  {tip.source.label}
                </a>
              )}
            </li>
          ))}
        </ul>
        <div className="mt-4 rounded-md border border-line bg-paper/60 px-3 py-2.5 text-sm">
          <Link href={SCHOLARSHIPS_HREF} className="link font-medium text-ink">
            Browse verified scholarships in AdmitGraph
          </Link>
          <span className="text-ink-faint">
            {" "}
            — source-linked funding, filterable by destination and degree level.
          </span>
        </div>
        <SourceLinks
          sources={
            country.fundingTips
              .map((tip) => tip.source)
              .filter((source): source is OfficialSource => source !== null)
          }
        />
      </Section>
    </main>
  );
}
