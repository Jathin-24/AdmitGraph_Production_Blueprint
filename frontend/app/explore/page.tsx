"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import { AuthNudge } from "../components/auth-nudge";
import {
  listPrograms,
  saveProgram,
  unsaveProgram,
  type ProgramListParams,
  type ProgramSort,
} from "../lib/api";

/** Debounce for the free-text search box — keeps keystrokes off the network
 *  while still feeling instant (P2-13b). */
const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 20;

const SORT_OPTIONS: { value: ProgramSort; label: string }[] = [
  { value: "fit_score", label: "Best fit" },
  { value: "name", label: "Name (A–Z)" },
  { value: "deadline", label: "Deadline (soonest)" },
];

const SORT_VALUES = SORT_OPTIONS.map((option) => option.value);

function normalizeSort(raw: string | null): ProgramSort {
  return raw && SORT_VALUES.includes(raw as ProgramSort) ? (raw as ProgramSort) : "fit_score";
}

function SaveButton({ programId, saved }: { programId: string; saved: boolean }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => (saved ? unsaveProgram(programId) : saveProgram(programId)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-programs"] }),
  });
  return (
    <button
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      aria-label={saved ? "Remove from my plan" : "Save to my plan"}
      className={saved ? "btn-secondary btn-sm" : "btn-ghost btn-sm"}
    >
      {saved ? "Saved ✓" : "Save"}
    </button>
  );
}

/** P2-13b: search + country/degree/sort filters + pagination, all synced to
 *  the URL (router.replace + searchParams) so any result view is shareable
 *  and the browser back button leaves the page cleanly instead of stepping
 *  through invisible filter history. */
function ExploreContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // --- URL state (single source of truth) ---
  const q = searchParams.get("q") ?? "";
  const country = searchParams.get("country") ?? "";
  const degreeLevel = searchParams.get("degree_level") ?? "";
  const sort = normalizeSort(searchParams.get("sort"));
  const savedOnly = searchParams.get("saved_only") === "true";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const hasFilters = q !== "" || country !== "" || degreeLevel !== "" || savedOnly || sort !== "fit_score";

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
  const [qDraft, setQDraft] = useState(q);
  // URL → box (clear filters, back/forward, shared links).
  useEffect(() => {
    setQDraft(q);
  }, [q]);
  // Box → URL (debounced), resetting to page 1 like every other filter.
  useEffect(() => {
    if (qDraft === q) return;
    const timer = setTimeout(() => updateParams({ q: qDraft, page: null }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDraft]);

  // --- data ---
  const params: ProgramListParams = {
    page,
    page_size: PAGE_SIZE,
    saved_only: savedOnly,
    q: q || undefined,
    country: country || undefined,
    degree_level: degreeLevel || undefined,
    sort,
  };
  const programs = useQuery({
    queryKey: ["programs", params],
    queryFn: () => listPrograms(params),
    placeholderData: (prev) => prev,
  });
  // Saved membership for the Save/Saved column — same queryKey + shape as
  // the program page, normalized to a Set via select.
  const saved = useQuery({
    queryKey: ["saved-programs"],
    queryFn: () => listPrograms(1, 100, true),
    select: (raw: unknown): Set<string> =>
      raw instanceof Set
        ? (raw as Set<string>)
        : new Set(
            ((raw as { items?: { id: string }[] })?.items ?? []).map((sp) => sp.id)
          ),
  });

  // Facet options are derived from real results (never a fabricated country
  // list) and accumulate across pages/filters so narrowing a filter doesn't
  // erase the other choices. A directly-shared filtered URL may only show
  // the values it contains — "All …" (the empty option) always clears it.
  const [facets, setFacets] = useState<{ countries: string[]; degrees: string[] }>({
    countries: [],
    degrees: [],
  });
  useEffect(() => {
    const items = programs.data?.items;
    if (!items || items.length === 0) return;
    setFacets((prev) => {
      const countries = new Set(prev.countries);
      const degrees = new Set(prev.degrees);
      for (const item of items) {
        if (item.country_code) countries.add(item.country_code);
        if (item.degree_type) degrees.add(item.degree_type);
      }
      if (country) countries.add(country);
      if (degreeLevel) degrees.add(degreeLevel);
      return {
        countries: Array.from(countries).sort(),
        degrees: Array.from(degrees).sort(),
      };
    });
  }, [programs.data, country, degreeLevel]);

  const total = programs.data?.total ?? 0;
  const pageSize = programs.data?.page_size ?? PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const items = useMemo(() => programs.data?.items ?? [], [programs.data]);
  const empty = programs.isSuccess && items.length === 0;

  function goToPage(next: number) {
    updateParams({ page: next <= 1 ? null : String(next) });
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Catalog"
        title="Explore programs"
        lede="Find real, sourced programs to compare — every row traces back to evidence from a research run, never to guesses."
      />

      <AuthNudge next="/explore">Sign in to keep saved programs in your own list.</AuthNudge>

      {/* Filters — every control writes to the URL so results are shareable */}
      <section className="card space-y-3 p-4" aria-label="Search and filters">
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="label">Search</span>
            <input
              type="search"
              className="field"
              placeholder="Program, university or city…"
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
              value={country}
              onChange={(event) => updateParams({ country: event.target.value, page: null })}
            >
              <option value="">All countries</option>
              {country && !facets.countries.includes(country) && (
                <option value={country}>{country}</option>
              )}
              {facets.countries.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Degree level</span>
            <select
              className="field md:w-44"
              value={degreeLevel}
              onChange={(event) => updateParams({ degree_level: event.target.value, page: null })}
            >
              <option value="">All levels</option>
              {degreeLevel && !facets.degrees.includes(degreeLevel) && (
                <option value={degreeLevel}>{degreeLevel}</option>
              )}
              {facets.degrees.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Sort by</span>
            <select
              className="field md:w-44"
              value={sort}
              onChange={(event) =>
                updateParams({ sort: event.target.value, page: null })
              }
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={savedOnly}
              onChange={(event) =>
                updateParams({ saved_only: event.target.checked ? "true" : null, page: null })
              }
              className="h-4 w-4 accent-[#1D5C46]"
            />
            Saved only
          </label>
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={!hasFilters}
            onClick={clearFilters}
          >
            Clear filters
          </button>
        </div>
      </section>

      {programs.isLoading && <LoadingNote what="Loading programs…" />}
      {!programs.isLoading && programs.isFetching && (
        <p role="status" className="text-xs text-ink-faint">
          Updating results…
        </p>
      )}
      {programs.isError && (
        <div className="space-y-3">
          <ErrorNote
            message={`Could not load programs: ${(programs.error as Error).message}. The backend may be restarting — try again in a moment.`}
          />
          <button type="button" className="btn-primary btn-sm" onClick={() => programs.refetch()}>
            Try again
          </button>
        </div>
      )}

      {/* No results — FRONTEND_SPEC §Error states: always explain WHY the
          list may be empty and offer one concrete next action. */}
      {empty && hasFilters && !savedOnly && (
        <EmptyState
          title="No programs match your filters"
          body="Nothing in the catalog matches this search and filter combination — widen the search or clear the filters to see everything."
          action={
            <button type="button" className="btn-primary btn-sm" onClick={clearFilters}>
              Clear filters
            </button>
          }
        />
      )}

      {empty && savedOnly && (
        <EmptyState
          title="Nothing saved yet"
          body="The “Saved only” filter is on and nothing matches it yet — press Save on a program in the full list and it lands in this shortlist."
          action={
            <button type="button" className="btn-primary btn-sm" onClick={clearFilters}>
              Show all programs
            </button>
          }
        />
      )}

      {empty && !hasFilters && (
        <EmptyState
          title={total > 0 ? "Nothing on this page" : "No programs yet"}
          body={
            total > 0
              ? "There are programs in the catalog, but none left on this page — the list may have changed since you moved forward."
              : "Nothing has been researched on this server yet — programs only appear once a research run discovers and verifies them."
          }
          action={
            total > 0 ? (
              <button type="button" className="btn-primary btn-sm" onClick={() => goToPage(page - 1)}>
                ← Back a page
              </button>
            ) : (
              <Link href="/research" className="btn-primary btn-sm">
                Run the full example
              </Link>
            )
          }
        />
      )}

      {items.length > 0 && (
        <div className="card overflow-hidden" aria-busy={programs.isFetching}>
          <div className="overflow-x-auto">
            <table className="table-editorial min-w-[640px]">
              <thead>
                <tr>
                  <th className="pl-5">Program</th>
                  <th>Country</th>
                  <th>Level</th>
                  <th>Field</th>
                  <th className="pr-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id}>
                    <td className="pl-5">
                      <Link
                        href={`/programs/${p.id}`}
                        className="display text-[15px] font-medium text-ink decoration-forest underline-offset-4 hover:underline"
                      >
                        {p.name}
                      </Link>
                    </td>
                    <td className="text-ink-soft">{p.country_code ?? "—"}</td>
                    <td className="text-ink-soft">{p.degree_type ?? "—"}</td>
                    <td className="text-ink-soft">{p.field_of_study ?? "—"}</td>
                    <td className="pr-5">
                      <div className="flex items-center justify-end gap-2">
                        {p.official_url && (
                          <a
                            href={p.official_url}
                            target="_blank"
                            rel="noreferrer"
                            className="link text-xs text-ink-faint"
                            aria-label={`Official site for ${p.name}`}
                          >
                            Official ↗
                          </a>
                        )}
                        <SaveButton programId={p.id} saved={saved.data?.has(p.id) ?? false} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <nav
            className="flex items-center justify-between border-t border-line px-5 py-3 text-sm text-ink-soft"
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
              Page {page} of {totalPages} · {total} programs
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
    </main>
  );
}

/** useSearchParams needs a Suspense boundary for prerendering (Next 14). */
export default function ExplorePage() {
  return (
    <Suspense fallback={<LoadingNote what="Loading programs…" />}>
      <ExploreContent />
    </Suspense>
  );
}
