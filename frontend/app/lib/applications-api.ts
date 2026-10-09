/**
 * W12 application tracker — API helpers + the pure grouping the page renders.
 *
 * All calls go through the shared `apiFetch` exported from `./api`
 * (PLAN.md cross-WS contract), so bearer auth, session-expiry handling and
 * the backend's error envelope behave exactly like every other page. This
 * is a NEW self-contained file: `api.ts` / `api-extra.ts` are shared and
 * must not be touched during the submission wave.
 */

import { apiFetch } from "./api";

/** Statuses the backend accepts — mirrors the API contract enum
 *  (draft|submitted|interview|offer|rejected|waitlist|withdrawn). */
export const APPLICATION_STATUSES = [
  "draft",
  "submitted",
  "interview",
  "offer",
  "rejected",
  "waitlist",
  "withdrawn",
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

/** One row of GET /applications — the exact shape the backend returns. */
export interface Application {
  id: string;
  university: string;
  program_name: string | null;
  status: ApplicationStatus;
  url: string | null;
  notes: string | null;
  submitted_at: string | null;
  decision_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationCreateInput {
  university: string;
  status: ApplicationStatus;
  program_name?: string | null;
  url?: string | null;
  notes?: string | null;
  submitted_at?: string | null;
  decision_at?: string | null;
}

/** Partial update: only the keys present are sent (the backend applies
 *  exactly those and leaves everything else untouched). */
export type ApplicationPatch = Partial<ApplicationCreateInput>;

/** GET /applications — `status` mirrors the backend's optional filter; the
 *  page itself loads everything once and groups client-side, because the
 *  per-status counts need the whole list anyway. */
export function listApplications(status?: ApplicationStatus): Promise<{ items: Application[] }> {
  return apiFetch(`/applications${status ? `?status=${status}` : ""}`);
}

export function createApplication(input: ApplicationCreateInput): Promise<Application> {
  return apiFetch("/applications", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function patchApplication(id: string, patch: ApplicationPatch): Promise<Application> {
  return apiFetch(`/applications/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteApplication(id: string): Promise<{ id: string; deleted: boolean }> {
  return apiFetch(`/applications/${id}`, { method: "DELETE" });
}

/* ------------------------------------------------------- grouping (pure) */

/** Human labels — the only status wording students see. */
export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  draft: "Draft",
  submitted: "Submitted",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  waitlist: "Waitlist",
  withdrawn: "Withdrawn",
};

/** Display order: nothing sent yet → in flight → outcomes. Groups the list
 *  by this order; a status missing from here simply doesn't produce a group. */
export const STATUS_ORDER: readonly ApplicationStatus[] = [
  "draft",
  "submitted",
  "interview",
  "waitlist",
  "offer",
  "rejected",
  "withdrawn",
];

/** Label for any status string — a value this build doesn't know yet falls
 *  back to itself (the raw value is honest; inventing a friendly name is not). */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status as ApplicationStatus] ?? status;
}

export interface ApplicationGroup {
  status: string;
  label: string;
  items: Application[];
}

/** Group rows by status in pipeline order, preserving each group's count as
 *  `items.length`. A status this build doesn't know yet (backend added one)
 *  still renders — appended last with the raw value as its label — so a
 *  student's row is never silently dropped from the page. */
export function groupApplications(items: readonly Application[]): ApplicationGroup[] {
  const buckets = new Map<string, Application[]>();
  for (const item of items) {
    const bucket = buckets.get(item.status);
    if (bucket) bucket.push(item);
    else buckets.set(item.status, [item]);
  }

  const groups: ApplicationGroup[] = [];
  const seen = new Set<string>();

  for (const status of STATUS_ORDER) {
    const bucket = buckets.get(status);
    if (bucket) {
      groups.push({ status, label: statusLabel(status), items: bucket });
      seen.add(status);
    }
  }
  // Map.forEach, not `for...of` over the Map: tsconfig has no ES2015+ target
  // so destructuring iteration trips TS2802 (downlevelIteration is off).
  buckets.forEach((bucket, status) => {
    if (!seen.has(status)) groups.push({ status, label: statusLabel(status), items: bucket });
  });
  return groups;
}

/** Per-status row counts for the chips above the grouped list. */
export function statusCounts(items: readonly Application[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
  }
  return counts;
}
