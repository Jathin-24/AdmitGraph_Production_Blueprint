"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import {
  SCHOLARSHIP_PAGE_SIZE,
  degreeLevelLabel,
  eligibilitySnippet,
  filtersFromSearchParams,
  formatDeadline,
  fundingTypeLabel,
  hasActiveFilters,
  listScholarships,
  type Scholarship,
  type ScholarshipListParams,
} from "../lib/scholarships-api";

/** Debounce for the free-text search box — keeps keystrokes off the network
 *  while still feeling instant (same pattern as /explore, P2-13b). */
const SEARCH_DEBOUNCE_MS = 300;

/** Human labels for the two enum dropdowns. The backend is the source of
 *  truth for accepted values; these must stay in sync with it. */
const DEGREE_OPTIONS = [
  { value: "bachelors", label: "Bachelor's" },
  { value: "masters", label: "Master's" },
  { value: "phd", label: "PhD" },
];

const FUNDING_OPTIONS = [
  { value: "full", label: "Full funding" },
  { value: "partial", label: "Partial" },
  { value: "merit", label: "Merit-based" },
  { value: "need-based", label: "Need-based" },
];

/** One scholarship card. Every fact traces back to `source_url`: the card
 *  always renders the provider, the verified-on date, and a link to the
 *  official page so a student can re-check figures before relying on them. */
function ScholarshipCard({ item }: { item: Scholarship }) {
  const deadline = formatDeadline(item.deadline);
  return (
    <article className="card flex flex-col gap-3 p-5" aria-label={item.name}>
      <div className="space-y-1">
        <h2 className="display text-lg font-medium leading-snug text-ink">{item.name}</h2>
        <p className="text-sm text-ink-soft">{item.provider}</p>
      </div>

      <div className="flex flex-wrap gap-1.5 text-xs">
        <span className="rounded-full border border-line px-2 py-0.5 text-ink-soft">
          {item.country ?? "Multiple countries"}
        </span>
        {item.degree_levels.map((level) => (
          <span key={level} className="rounded-full border border-line px-2 py-0.5 text-ink-soft">
            {degreeLevelLabel(level)}
          </span>
        ))}
        <span className="rounded-full border border-line px-2 py-0.5 text-ink-soft">
          {fundingTypeLabel(item.funding_type)}
        </span>
      </div>

      <div className="space-y-1 text-sm">
        <p className="font-medium text-ink">{item.amount_text ?? "Amount not stated on the source page"}</p>
        <p className="text-ink-soft">
          Deadline:{" "}
          {deadline ? (
            <span className="font-medium text-ink">{deadline}</span>
          ) : (
            <span className="text-ink-faint">
              no single date published — each course or commission sets its own; check the source
            </span>
          )}
        </p>
      </div>

      <p className="text-sm leading-relaxed text-ink-soft">{eligibilitySnippet(item.eligibility, 220)}</p>

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
        <span className="text-xs text-ink-faint">Verified {item.last_checked} · figures can change — re-check at source</span>
        <div className="flex gap-3 text-xs">
          <a
            href={item.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="link text-forest"
            aria-label={`Official source for ${item.name} (opens in a new tab)`}
          >
            Source ↗
          </a>
          {item.details_url && (
            <a
              href={item.details_url}
              target="_blank"
              rel="noopener noreferrer"
              className="link text-forest"
              aria-label={`More details for ${item.name} (opens in a new tab)`}
            >
              Details ↗
            </a>
          )}
        </div>
      </div>
    </article>
  );
}

/** W13: search + country/degree/funding filters, all synced to the URL
 *  (router.replace + searchParams) so any result view is shareable and the
 *  browser back button leaves the page cleanly — same contract as
 *  /explore (P2-13b). */
function ScholarshipsContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // --- URL state (single source of truth) ---
  const filters = filtersFromSearchParams(searchParams);
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const active = hasActiveFilters(filters);

  /** Merge a patch into the current query string; empty/null removes a key. */
  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, value);
      }
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams]
  );

  const clearFilters = useCallback(() => {
    setQDraft("");
    router.replace(pathname, { scroll: false });
  }, [router, pathname]);

  // --- debounced search box ---
  const [qDraft, setQDraft] = useState(filters.q);
  // URL → box (clear filters, back/forward, shared links).
  useEffect(() => {
    setQDraft(filters.q);
  }, [filters.q]);
  // Box → URL (debounced), resetting to page 1 like every other filter.
  useEffect(() => {
    if (qDraft === filters.q) return;
    const timer = setTimeout(() => updateParams({ q: qDraft, page: null }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDraft]);

  // --- data ---
  const params: ScholarshipListParams = {
    page,
    page_size: SCHOLARSHIP_PAGE_SIZE,
    q: filters.q || undefined,
    country: filters.country || undefined,
    degree_level: filters.degree_level || undefined,
    funding_type: filters.funding_type || undefined,
  };
  const scholarships = useQuery({
    queryKey: ["scholarships", params],
    queryFn: () => listScholarships(params),
    placeholderData: (prev) => prev,
  });

  // Country facet options are derived from real results (never a fabricated
  // list) and accumulate across pages/filters, mirroring /explore. A
  // directly-shared filtered URL may only show the values it contains —
  // "All countries" (the empty option) always clears it.
  const [countryFacets, setCountryFacets] = useState<string[]>([]);
  useEffect(() => {
    const items = scholarships.data?.items;
    if (!items || items.length === 0) return;
    setCountryFacets((prev) => {
      const codes = new Set(prev);
      for (const item of items) {
        if (item.country) codes.add(item.country);
      }
      if (filters.country) codes.add(filters.country);
      return Array.from(codes).sort();
    });
  }, [scholarships.data, filters.country]);

  const total = scholarships.data?.total ?? 0;
  const pageSize = scholarships.data?.page_size ?? SCHOLARSHIP_PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const items = useMemo(() => scholarships.data?.items ?? [], [scholarships.data]);
  const empty = scholarships.isSuccess && items.length === 0;

  function goToPage(next: number) {
    updateParams({ page: next <= 1 ? null : String(next) });
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Funding"
        title="Scholarship finder"
        lede="Every scholarship here was read off its official source page on the date shown on its card — open the source link to re-check the figures before you rely on them."
      />

      {/* Filters — every control writes to the URL so results are shareable */}
      <section className="card space-y-3 p-4" aria-label="Search and filters">
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="label">Search</span>
            <input
              type="search"
              className="field"
              placeholder="Scholarship, provider or eligibility…"
              value={qDraft}
              onChange={(event) => setQDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  updateParams({ q: qDraft, page: null });
                }
              }}
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Country</span>
            <select
              className="field md:w-40"
              value={filters.country}
              onChange={(event) => updateParams({ country: event.target.value, page: null })}
            >
              <option value="">All countries</option>
              {filters.country && !countryFacets.includes(filters.country) && (
                <option value={filters.country}>{filters.country}</option>
              )}
              {countryFacets.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Degree level</span>
            <select
              className="field md:w-40"
              value={filters.degree_level}
              onChange={(event) => updateParams({ degree_level: event.target.value, page: null })}
            >
              <option value="">All levels</option>
              {filters.degree_level && !DEGREE_OPTIONS.some((o) => o.value === filters.degree_level) && (
                <option value={filters.degree_level}>{filters.degree_level}</option>
              )}
              {DEGREE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Funding</span>
            <select
              className="field md:w-40"
              value={filters.funding_type}
              onChange={(event) => updateParams({ funding_type: event.target.value, page: null })}
            >
              <option value="">All types</option>
              {filters.funding_type &&
                !FUNDING_OPTIONS.some((o) => o.value === filters.funding_type) && (
                  <option value={filters.funding_type}>{filters.funding_type}</option>
                )}
              {FUNDING_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-ink-faint">
            Filters match the dataset exactly — unknown values simply return no rows.
          </span>
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={!active && page <= 1}
            onClick={clearFilters}
          >
            Clear filters
          </button>
        </div>
      </section>

      {scholarships.isLoading && <LoadingNote what="Loading scholarships…" />}
      {!scholarships.isLoading && scholarships.isFetching && (
        <p role="status" className="text-xs text-ink-faint">
          Updating results…
        </p>
      )}
      {scholarships.isError && (
        <div className="space-y-3">
          <ErrorNote
            message={`Could not load scholarships: ${(scholarships.error as Error).message}. The backend may be restarting — try again in a moment.`}
          />
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => scholarships.refetch()}
          >
            Try again
          </button>
        </div>
      )}

      {/* No results — FRONTEND_SPEC §Error states: always explain WHY the
          list may be empty and offer one concrete next action. */}
      {empty && active && (
        <EmptyState
          title="No scholarships match your filters"
          body="Nothing in the verified dataset matches this search and filter combination — widen the search or clear the filters to see everything."
          action={
            <button type="button" className="btn-primary btn-sm" onClick={clearFilters}>
              Clear filters
            </button>
          }
        />
      )}

      {empty && !active && (
        <EmptyState
          title={total > 0 ? "Nothing on this page" : "No scholarships yet"}
          body={
            total > 0
              ? "There are scholarships in the dataset, but none left on this page — the list may have changed since you moved forward."
              : "The verified scholarship dataset is empty on this server — entries appear once someone reads and checks them at their official source."
          }
          action={
            total > 0 ? (
              <button
                type="button"
                className="btn-primary btn-sm"
                onClick={() => goToPage(page - 1)}
              >
                ← Back a page
              </button>
            ) : undefined
          }
        />
      )}

      {items.length > 0 && (
        <div className="space-y-4" aria-busy={scholarships.isFetching}>
          <div className="grid gap-4 md:grid-cols-2">
            {items.map((item) => (
              <ScholarshipCard key={item.id} item={item} />
            ))}
          </div>

          <nav
            className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-sm text-ink-soft"
            aria-label="Pagination"
          >
            <button
              className="btn-ghost btn-sm"
              disabled={page <= 1}
              onClick={() => goToPage(page - 1)}
            >
              ← Previous
            </button>
            <span className="text-xs uppercase tracking-wide text-ink-faint">
              Page {page} of {totalPages} · {total} scholarships
            </span>
            <button
              className="btn-ghost btn-sm"
              disabled={page >= totalPages}
              onClick={() => goToPage(page + 1)}
            >
              Next →
            </button>
          </nav>
        </div>
      )}

      {items.length > 0 && (
        <p className="text-xs leading-relaxed text-ink-faint">
          This is a curated starter set, not an exhaustive catalogue. Amounts and deadlines
          change between application cycles — every figure above was read from the linked
          official page on the date shown on its card, and should be re-verified there before
          you build a plan around it. Nothing here is personalised advice.
        </p>
      )}
    </main>
  );
}

/** useSearchParams needs a Suspense boundary for prerendering (Next 14). */
export default function ScholarshipsPage() {
  return (
    <Suspense fallback={<LoadingNote what="Loading scholarships…" />}>
      <ScholarshipsContent />
    </Suspense>
  );
}
