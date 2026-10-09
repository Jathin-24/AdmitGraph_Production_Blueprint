/**
 * W12 — application tracker helpers.
 *
 * Pins the two properties the /applications page relies on:
 *  1. grouping/counting is pure, pipeline-ordered, and never drops a row —
 *     even a status this build doesn't recognise still renders (raw value
 *     as its label) instead of silently disappearing from a student's list;
 *  2. the list call goes through the shared apiFetch and appends the
 *     backend's optional `?status=` filter only when one was asked for.
 *
 * No network: `fetch` is stubbed per test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE } from "./api";
import {
  APPLICATION_STATUSES,
  STATUS_ORDER,
  groupApplications,
  listApplications,
  statusCounts,
  statusLabel,
  type Application,
} from "./applications-api";

function row(id: string, status: string, university = "TU Munich"): Application {
  return {
    id,
    university,
    program_name: null,
    status: status as Application["status"],
    url: null,
    notes: null,
    submitted_at: null,
    decision_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("groupApplications", () => {
  it("groups rows in pipeline order with per-group counts", () => {
    const groups = groupApplications([
      row("a", "offer"),
      row("b", "draft"),
      row("c", "draft"),
      row("d", "submitted"),
    ]);

    expect(groups.map((g) => [g.status, g.items.length])).toEqual([
      ["draft", 2],
      ["submitted", 1],
      ["offer", 1],
    ]);
    expect(groups[0].label).toBe("Draft");
    // Insertion order is preserved inside a group (list order from the API).
    expect(groups[0].items.map((item) => item.id)).toEqual(["b", "c"]);
  });

  it("returns no groups for an empty tracker", () => {
    expect(groupApplications([])).toEqual([]);
  });

  it("keeps rows whose status this build doesn't know — never dropped", () => {
    const groups = groupApplications([row("a", "deferred_2027"), row("b", "draft")]);

    expect(groups.map((g) => g.status)).toEqual(["draft", "deferred_2027"]);
    // Raw value as the label: honest fallback, no invented wording.
    expect(groups[1].label).toBe("deferred_2027");
    expect(groups[1].items.map((item) => item.id)).toEqual(["a"]);
  });

  it("STATUS_ORDER covers every contract status exactly once", () => {
    // Guards the counts strip: a status added to the contract but not
    // ordered would render a group without a pipeline position.
    expect([...STATUS_ORDER].sort()).toEqual([...APPLICATION_STATUSES].sort());
  });
});

describe("statusCounts", () => {
  it("counts every status exactly", () => {
    expect(
      statusCounts([row("a", "draft"), row("b", "draft"), row("c", "waitlist")])
    ).toEqual({ draft: 2, waitlist: 1 });
  });

  it("returns an empty record for an empty tracker", () => {
    expect(statusCounts([])).toEqual({});
  });
});

describe("statusLabel", () => {
  it("labels known statuses and echoes unknown ones verbatim", () => {
    expect(statusLabel("offer")).toBe("Offer");
    expect(statusLabel("something_new")).toBe("something_new");
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
  fetchMock = vi.fn(async () => jsonResponse(200, { items: [] }));
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

describe("listApplications", () => {
  it("hits /applications and adds ?status= only when a filter is given", async () => {
    await listApplications();
    expect(lastUrl()).toBe(`${API_BASE}/applications`);

    await listApplications("offer");
    expect(lastUrl()).toBe(`${API_BASE}/applications?status=offer`);
  });
});
