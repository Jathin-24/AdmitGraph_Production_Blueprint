/**
 * W13 scholarship finder — API helpers + the pure URL/deadline helpers the
 * /scholarships page renders.
 *
 * All calls go through the shared `apiFetch` exported from `./api`
 * (PLAN.md cross-WS contract), so bearer auth, session-expiry handling and
 * the backend's error envelope behave exactly like every other page. This
 * is a NEW self-contained file: `api.ts` / `api-extra.ts` are shared and
 * must not be touched during the submission wave.
 *
 * The dataset is curated reference data, not a live database: every row the
 * backend serves carries the `last_checked` date it was verified at its
 * official source, plus a `source_url` a student can open to re-check the
 * figures themselves. Amounts and deadlines change between cycles — the UI
 * must always show the source link next to the facts.
 */

import { apiFetch } from "./api";

/** Enumerations the backend filter accepts — mirrors the dataset contract
 *  (backend app/services/scholarships.py). */
export const SCHOLARSHIP_DEGREE_LEVELS = ["bachelors", "masters", "phd"] as const;
export const SCHOLARSHIP_FUNDING_TYPES = ["full", "partial", "merit", "need-based"] as const;

export type ScholarshipDegreeLevel = (typeof SCHOLARSHIP_DEGREE_LEVELS)[number];
export type ScholarshipFundingType = (typeof SCHOLARSHIP_FUNDING_TYPES)[number];

/** One row of GET /scholarships — the exact shape the backend returns.
 *  `country`, `amount_text` and `deadline` may be null: a figure the source
 *  never stated is reported as missing, never filled in. */
export interface Scholarship {
  id: string;
  name: string;
  provider: string;
  country: string | null;
  degree_levels: string[];
  funding_type: string;
  amount_text: string | null;
  deadline: string | null;
  eligibility: string;
  source_url: string;
  details_url: string | null;
  last_checked: string;
}

export interface ScholarshipListParams {
  q?: string;
  country?: string;
  degree_level?: string;
  funding_type?: string;
  page?: number;
  page_size?: number;
}

/** Programs-shaped envelope (same keys as GET /programs) so the paging UI
 *  can be shared across both pages. */
export interface ScholarshipListResponse {
  items: Scholarship[];
  page: number;
  page_size: number;
  total: number;
  next_cursor: string | null;
}

export const SCHOLARSHIP_PAGE_SIZE = 20;

/** Query string for one list call: empty values are omitted entirely so the
 *  request stays minimal and cache keys stay stable. Page 1 and the default
 *  page size are omitted too — they are what the backend serves anyway. */
export function buildScholarshipQuery(params: ScholarshipListParams): string {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.country) search.set("country", params.country);
  if (params.degree_level) search.set("degree_level", params.degree_level);
  if (params.funding_type) search.set("funding_type", params.funding_type);
  if (params.page && params.page > 1) search.set("page", String(params.page));
  if (params.page_size && params.page_size !== SCHOLARSHIP_PAGE_SIZE) {
    search.set("page_size", String(params.page_size));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function listScholarships(
  params: ScholarshipListParams = {}
): Promise<ScholarshipListResponse> {
  return apiFetch(`/scholarships${buildScholarshipQuery(params)}`);
}

/* ------------------------------------------------- URL <-> filter state */

/** The four filters the page syncs to the URL — same param names the
 *  backend reads, so a shared link reproduces the exact result view. */
export interface ScholarshipFilters {
  q: string;
  country: string;
  degree_level: string;
  funding_type: string;
}

export const EMPTY_FILTERS: ScholarshipFilters = {
  q: "",
  country: "",
  degree_level: "",
  funding_type: "",
};

/** URLSearchParams → filter state. Unknown keys are ignored; a missing key
 *  is "" (the "All" option), never undefined, so controlled <select>s always
 *  have a value. */
export function filtersFromSearchParams(params: URLSearchParams): ScholarshipFilters {
  return {
    q: params.get("q") ?? "",
    country: params.get("country") ?? "",
    degree_level: params.get("degree_level") ?? "",
    funding_type: params.get("funding_type") ?? "",
  };
}

export function hasActiveFilters(filters: ScholarshipFilters): boolean {
  return (
    filters.q !== "" ||
    filters.country !== "" ||
    filters.degree_level !== "" ||
    filters.funding_type !== ""
  );
}

/** Filter state → query string ("" when nothing is set). Round-trips with
 *  filtersFromSearchParams: parsing the result returns the same filters. */
export function filtersToQueryString(filters: ScholarshipFilters): string {
  return buildScholarshipQuery(filters);
}

/* ------------------------------------------------------ display helpers */

const DEADLINE_MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** ISO date ("2027-03-01") → "Mar 1, 2027". Null stays null — the page then
 *  shows "Check the source", because "no deadline published" is a real state
 *  (each institution sets its own date), not a missing value to hide. A
 *  string this build can't parse is shown raw rather than reinterpreted. */
export function formatDeadline(deadline: string | null): string | null {
  if (!deadline) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(deadline);
  if (!match) return deadline;
  const monthName = DEADLINE_MONTHS[Number(match[2]) - 1];
  if (!monthName) return deadline;
  return `${monthName} ${Number(match[3])}, ${match[1]}`;
}

/** Human labels — unknown values fall back to the raw value so a backend
 *  addition never renders as blank. */
export const DEGREE_LEVEL_LABELS: Record<string, string> = {
  bachelors: "Bachelor's",
  masters: "Master's",
  phd: "PhD",
};

export const FUNDING_TYPE_LABELS: Record<string, string> = {
  full: "Full funding",
  partial: "Partial",
  merit: "Merit-based",
  "need-based": "Need-based",
};

export function degreeLevelLabel(level: string): string {
  return DEGREE_LEVEL_LABELS[level] ?? level;
}

export function fundingTypeLabel(fundingType: string): string {
  return FUNDING_TYPE_LABELS[fundingType] ?? fundingType;
}

/** Word-boundary snippet for the eligibility line on each card — the full
 *  sentence stays on the card when it fits, longer text is cut cleanly. */
export function eligibilitySnippet(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const cut = text.slice(0, maxChars);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > maxChars / 2 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}
