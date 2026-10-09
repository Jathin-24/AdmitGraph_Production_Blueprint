/**
 * W13 — scholarship finder helpers.
 *
 * Pins the properties the /scholarships page relies on:
 *  1. the URL <-> filter-state round trip — a shared link reproduces the
 *     exact filters, and clearing filters produces an empty query string;
 *  2. the list call goes through the shared apiFetch, sends only the
 *     filters that are set, and omits page 1 / the default page size;
 *  3. display helpers are honest: a null deadline stays null (the page
 *     shows "Check the source" — no date is ever invented), an unparseable
 *     date string is echoed raw, and unknown enum values fall back to
 *     themselves instead of rendering blank.
 *
 * No network: `fetch` is stubbed per test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE } from "./api";
import {
  EMPTY_FILTERS,
  buildScholarshipQuery,
  degreeLevelLabel,
  eligibilitySnippet,
  filtersFromSearchParams,
  filtersToQueryString,
  formatDeadline,
  fundingTypeLabel,
  hasActiveFilters,
  listScholarships,
  type ScholarshipFilters,
} from "./scholarships-api";

function filters(overrides: Partial<ScholarshipFilters>): ScholarshipFilters {
  return { ...EMPTY_FILTERS, ...overrides };
}

/* ------------------------------------------------- URL <-> filter state */

describe("filtersFromSearchParams", () => {
  it("reads the four filter keys and defaults each to the empty string", () => {
    const parsed = filtersFromSearchParams(
      new URLSearchParams("q=daad&country=DE&degree_level=masters&funding_type=full")
    );
    expect(parsed).toEqual({
      q: "daad",
      country: "DE",
      degree_level: "masters",
      funding_type: "full",
    });
  });

  it("ignores unknown keys and returns '' (not undefined) for missing ones", () => {
    const parsed = filtersFromSearchParams(new URLSearchParams("sort=deadline&saved_only=true"));
    expect(parsed).toEqual(EMPTY_FILTERS);
    // Controlled <select>s need a real value, never undefined.
    expect(parsed.country).toBe("");
  });
});

describe("filtersToQueryString", () => {
  it("round-trips through filtersFromSearchParams", () => {
    const original = filters({ q: "women in stem", country: "NL", funding_type: "need-based" });
    const parsed = filtersFromSearchParams(new URLSearchParams(filtersToQueryString(original)));
    expect(parsed).toEqual(original);
  });

  it("is '' when nothing is set (clear-filters leaves a clean URL)", () => {
    expect(filtersToQueryString(EMPTY_FILTERS)).toBe("");
  });
});

describe("hasActiveFilters", () => {
  it("is false only when every filter is empty", () => {
    expect(hasActiveFilters(EMPTY_FILTERS)).toBe(false);
    expect(hasActiveFilters(filters({ q: "x" }))).toBe(true);
    expect(hasActiveFilters(filters({ country: "DE" }))).toBe(true);
  });
});

describe("buildScholarshipQuery", () => {
  it("omits empty values, page 1 and the default page size", () => {
    expect(buildScholarshipQuery({})).toBe("");
    expect(buildScholarshipQuery({ q: "fulbright", page: 1, page_size: 20 })).toBe("?q=fulbright");
  });

  it("includes set filters, later pages and non-default sizes, in contract order", () => {
    expect(
      buildScholarshipQuery({
        q: "chevening",
        country: "GB",
        degree_level: "masters",
        funding_type: "full",
        page: 3,
        page_size: 50,
      })
    ).toBe("?q=chevening&country=GB&degree_level=masters&funding_type=full&page=3&page_size=50");
  });
});

/* ------------------------------------------------------ display helpers */

describe("formatDeadline", () => {
  it("formats an ISO date for display", () => {
    expect(formatDeadline("2027-03-01")).toBe("Mar 1, 2027");
    expect(formatDeadline("2026-12-15")).toBe("Dec 15, 2026");
  });

  it("keeps null as null — the page shows 'Check the source', never a guess", () => {
    expect(formatDeadline(null)).toBeNull();
  });

  it("echoes a string it cannot parse instead of reinterpreting it", () => {
    expect(formatDeadline("March 2027")).toBe("March 2027");
    expect(formatDeadline("2027-13-45")).toBe("2027-13-45");
  });
});

describe("degreeLevelLabel / fundingTypeLabel", () => {
  it("labels known values and echoes unknown ones verbatim", () => {
    expect(degreeLevelLabel("masters")).toBe("Master's");
    expect(fundingTypeLabel("need-based")).toBe("Need-based");
    // A value a future backend adds renders as itself, never blank.
    expect(degreeLevelLabel("associates")).toBe("associates");
    expect(fundingTypeLabel("in-kind")).toBe("in-kind");
  });
});

describe("eligibilitySnippet", () => {
  it("returns short text unchanged", () => {
    expect(eligibilitySnippet("Two sentences fit.", 80)).toBe("Two sentences fit.");
  });

  it("cuts long text at a word boundary and appends an ellipsis", () => {
    const text = "Applicants need outstanding academic merit and two references from faculty.";
    const snippet = eligibilitySnippet(text, 40);
    expect(snippet.endsWith("…")).toBe(true);
    expect(snippet.length).toBeLessThanOrEqual(40);
    expect(text.startsWith(snippet.slice(0, -1))).toBe(true);
    // Cut lands on a word boundary, not mid-word.
    expect(snippet.slice(0, -1).endsWith(" ")).toBe(false);
  });
});

/* ------------------------------------------------------------- list call */

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  localStorage.clear();
  fetchMock = vi.fn(async () => jsonResponse(200, { items: [], total: 0 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function lastUrl(): unknown {
  expect(fetchMock).toHaveBeenCalled();
  return fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0];
}

describe("listScholarships", () => {
  it("hits /scholarships and appends only the filters that are set", async () => {
    await listScholarships();
    expect(lastUrl()).toBe(`${API_BASE}/scholarships`);

    await listScholarships({ country: "DE", degree_level: "phd" });
    expect(lastUrl()).toBe(`${API_BASE}/scholarships?country=DE&degree_level=phd`);
  });
});
