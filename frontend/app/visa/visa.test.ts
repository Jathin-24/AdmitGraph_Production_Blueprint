/**
 * W14 — critical-path tests for the /visa helpers (PLAN.md W14 acceptance).
 *
 * Pins the three properties the page relies on:
 *  1. the storage key builder produces the documented per-country key
 *     (`admitgraph_visa_docs_<cc>`) and countries never collide,
 *  2. completion math is honest (0 when nothing to tick, whole percent,
 *     clamped at 100 — never a fake 100 for an empty checklist),
 *  3. the country dataset stays complete and honest: every country has
 *     non-empty steps + document lists, every citation resolves to a known
 *     official domain over https, and any numeric figure carries its own
 *     official source (the page renders it with the verify-at-source note).
 *
 * No network: the dataset is static and URLs are asserted structurally.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  COUNTRIES,
  LAST_CHECKED,
  SCHOLARSHIPS_HREF,
  VERIFY_FIGURES_NOTE,
} from "./data";
import {
  clearCheckedDocs,
  completionPercent,
  loadCheckedDocs,
  saveCheckedDocs,
  toggleChecked,
  visaDocsStorageKey,
} from "./storage";

/** Official domains the dataset is allowed to cite (all fetched HTTP 2xx
 *  when LAST_CHECKED was set — see the W14 report). */
const OFFICIAL_HOST_SUFFIXES = [
  "auswaertiges-amt.de", // German Federal Foreign Office
  "daad.de", // German Academic Exchange Service
  "study-in-germany.de", // DAAD student portal
  "ind.nl", // Dutch immigration service
  "educationusa.state.gov", // U.S. Department of State
  "gov.uk", // UK government
  "canada.ca", // Immigration, Refugees and Citizenship Canada
  "studyaustralia.gov.au", // Australian Government study portal
  "homeaffairs.gov.au", // Australian immigration authority
];

function hostOf(url: string): string {
  return new URL(url).hostname.toLowerCase();
}

function isOfficialHost(url: string): boolean {
  const host = hostOf(url);
  return OFFICIAL_HOST_SUFFIXES.some(
    (suffix) => host === suffix || host.endsWith(`.${suffix}`)
  );
}

/* ------------------------------------------------------ storage keys */

describe("visaDocsStorageKey", () => {
  it("builds the documented per-country key", () => {
    expect(visaDocsStorageKey("DE")).toBe("admitgraph_visa_docs_de");
    expect(visaDocsStorageKey("UK")).toBe("admitgraph_visa_docs_uk");
    expect(visaDocsStorageKey("AU")).toBe("admitgraph_visa_docs_au");
  });

  it("normalises case and stray whitespace so lookups always hit", () => {
    expect(visaDocsStorageKey("  nl ")).toBe(visaDocsStorageKey("nl"));
    expect(visaDocsStorageKey("ca")).toBe("admitgraph_visa_docs_ca");
  });

  it("never collides across countries", () => {
    const keys = ["DE", "NL", "US", "UK", "CA", "AU"].map(visaDocsStorageKey);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

/* ------------------------------------------------- completion percent */

describe("completionPercent", () => {
  it("is 0 for an empty checklist — never a fake 100", () => {
    expect(completionPercent([], 7)).toBe(0);
  });

  it("is 0 when there is nothing to tick", () => {
    expect(completionPercent([], 0)).toBe(0);
    expect(completionPercent(["passport"], 0)).toBe(0);
    expect(completionPercent(["passport"], -3)).toBe(0);
  });

  it("returns whole percentages with rounding", () => {
    expect(completionPercent(["a"], 3)).toBe(33); // 33.3… → 33
    expect(completionPercent(["a", "b"], 3)).toBe(67); // 66.6… → 67
    expect(completionPercent(["a", "b"], 4)).toBe(50);
    expect(completionPercent(["a", "b", "c"], 3)).toBe(100);
  });

  it("clamps to 100 even if a corrupt stored array over-counts", () => {
    expect(completionPercent(["a", "a", "a", "a"], 3)).toBe(100);
  });
});

/* ------------------------------------------------- checklist toggling */

describe("toggleChecked", () => {
  it("adds an id that is not ticked", () => {
    expect(toggleChecked(["passport"], "admission")).toEqual(["passport", "admission"]);
  });

  it("removes an id that is already ticked", () => {
    expect(toggleChecked(["passport", "admission"], "passport")).toEqual(["admission"]);
  });

  it("does not mutate the input array", () => {
    const before = ["passport"];
    toggleChecked(before, "admission");
    expect(before).toEqual(["passport"]);
  });
});

/* ------------------------------------------- per-country persistence */

describe("checklist persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("round-trips checked ids for one country only", () => {
    saveCheckedDocs("DE", ["passport", "admission"]);
    expect(loadCheckedDocs("DE")).toEqual(["passport", "admission"]);
    expect(loadCheckedDocs("NL")).toEqual([]);
  });

  it("returns [] for corrupt stored values instead of throwing", () => {
    localStorage.setItem(visaDocsStorageKey("US"), "{not json");
    expect(loadCheckedDocs("US")).toEqual([]);

    localStorage.setItem(visaDocsStorageKey("US"), '"a string, not an array"');
    expect(loadCheckedDocs("US")).toEqual([]);

    // Mixed arrays keep only the string ids (defensive against drift).
    localStorage.setItem(visaDocsStorageKey("US"), '["ok", 7, null]');
    expect(loadCheckedDocs("US")).toEqual(["ok"]);
  });

  it("clearCheckedDocs removes only the requested country", () => {
    saveCheckedDocs("UK", ["cas"]);
    saveCheckedDocs("CA", ["acceptance"]);
    clearCheckedDocs("UK");
    expect(loadCheckedDocs("UK")).toEqual([]);
    expect(loadCheckedDocs("CA")).toEqual(["acceptance"]);
  });
});

/* --------------------------------------------- country data integrity */

describe("country data integrity", () => {
  it("covers exactly DE, NL, US, UK, CA, AU in selector order", () => {
    expect(COUNTRIES.map((c) => c.code)).toEqual(["DE", "NL", "US", "UK", "CA", "AU"]);
  });

  it("every country has non-empty steps and a document checklist", () => {
    for (const country of COUNTRIES) {
      expect(country.steps.length, `${country.code} steps`).toBeGreaterThan(1);
      expect(country.documents.length, `${country.code} documents`).toBeGreaterThan(0);
      expect(country.financialProof.forms.length, `${country.code} forms`).toBeGreaterThan(0);
      expect(country.fundingTips.length, `${country.code} funding tips`).toBeGreaterThan(0);
      expect(country.visaName.length, `${country.code} visa name`).toBeGreaterThan(0);
    }
  });

  it("every section of every country cites at least one official source URL", () => {
    for (const country of COUNTRIES) {
      expect(country.stepsSources.length, `${country.code} steps source`).toBeGreaterThan(0);
      expect(country.financialSources.length, `${country.code} financial source`).toBeGreaterThan(0);
      expect(country.documentSources.length, `${country.code} documents source`).toBeGreaterThan(0);
      const sourcedTips = country.fundingTips.filter((tip) => tip.source !== null);
      expect(sourcedTips.length, `${country.code} sourced funding tip`).toBeGreaterThan(0);
    }
  });

  it("every citation resolves to a known official domain over https", () => {
    for (const country of COUNTRIES) {
      const sources = [
        ...country.stepsSources,
        ...country.financialSources,
        ...country.documentSources,
        ...country.fundingTips.map((tip) => tip.source),
        country.financialProof.figure?.source,
      ];
      for (const source of sources) {
        if (!source) continue;
        expect(source.label.length, `${country.code} label`).toBeGreaterThan(0);
        expect(source.url.startsWith("https://"), source.url).toBe(true);
        expect(isOfficialHost(source.url), `${source.url} is not an official host`).toBe(true);
      }
    }
  });

  it("any numeric figure carries its own official source and the verify note exists", () => {
    const withFigure = COUNTRIES.filter((c) => c.financialProof.figure);
    // Figures are optional — only some countries have a verified amount.
    expect(withFigure.length).toBeGreaterThan(0);
    for (const country of withFigure) {
      const figure = country.financialProof.figure;
      expect(figure, `${country.code} figure`).toBeDefined();
      if (!figure) continue;
      expect(figure.amount.trim().length).toBeGreaterThan(0);
      expect(figure.source.url.startsWith("https://")).toBe(true);
      expect(isOfficialHost(figure.source.url)).toBe(true);
      expect(figure.source.label.length).toBeGreaterThan(0);
    }
    // The page renders this next to every figure.
    expect(VERIFY_FIGURES_NOTE.toLowerCase()).toContain("verify");
    expect(VERIFY_FIGURES_NOTE.toLowerCase()).toContain("source");
  });

  it("document ids are unique within each country (checklist storage depends on it)", () => {
    for (const country of COUNTRIES) {
      const ids = country.documents.map((doc) => doc.id);
      expect(new Set(ids).size, `${country.code} duplicate doc ids`).toBe(ids.length);
      for (const id of ids) expect(id.length).toBeGreaterThan(0);
    }
  });

  it("funding tips cross-link to the internal scholarship finder", () => {
    expect(SCHOLARSHIPS_HREF).toBe("/scholarships");
  });

  it("last-checked date is a real ISO date", () => {
    expect(LAST_CHECKED).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(Number.isNaN(Date.parse(LAST_CHECKED))).toBe(false);
  });
});
