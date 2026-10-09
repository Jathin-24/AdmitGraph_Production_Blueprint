/**
 * W14 — localStorage helpers for the /visa document checklist.
 *
 * Storage contract (pinned by visa.test.ts):
 *   key    = `admitgraph_visa_docs_<country-code>` (lower-cased)
 *   value  = JSON array of checked document ids, e.g. `["passport","i-20"]`
 *
 * SSR safety mirrors app/lib/api.ts token storage: every read/write is
 * gated on `typeof window`, and every failure (private mode, quota,
 * corrupt JSON) degrades to "no persistence" instead of throwing — the
 * page keeps working from in-memory state.
 */

/** localStorage key for a country's checked-document list. */
export function visaDocsStorageKey(countryCode: string): string {
  return `admitgraph_visa_docs_${countryCode.trim().toLowerCase()}`;
}

/** Read the checked document ids for a country. Never throws. */
export function loadCheckedDocs(countryCode: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(visaDocsStorageKey(countryCode));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

/** Persist the checked document ids for a country. Never throws. */
export function saveCheckedDocs(countryCode: string, docIds: string[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(visaDocsStorageKey(countryCode), JSON.stringify(docIds));
  } catch {
    /* storage unavailable — checklist still works in memory */
  }
}

/** Remove a country's saved checklist (the page's "clear" action). */
export function clearCheckedDocs(countryCode: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(visaDocsStorageKey(countryCode));
  } catch {
    /* storage unavailable — nothing to clear */
  }
}

/** Add or remove one document id — pure, so tests can pin toggle behaviour. */
export function toggleChecked(checked: readonly string[], docId: string): string[] {
  return checked.includes(docId)
    ? checked.filter((id) => id !== docId)
    : [...checked, docId];
}

/**
 * Checklist completion as a whole percentage (0–100).
 * An empty checklist is 0 — never a fake 100. Over-counting (duplicate ids
 * in the stored array) is clamped to 100 rather than shown as >100%.
 */
export function completionPercent(checked: readonly string[], totalDocs: number): number {
  if (totalDocs <= 0) return 0;
  const percent = Math.round((checked.length / totalDocs) * 100);
  return Math.min(100, Math.max(0, percent));
}
