/**
 * P2-24 — critical-path tests for the authenticated API client.
 *
 * These pin the three properties every page relies on:
 *  1. the stored bearer token rides on every request,
 *  2. caller-supplied headers merge instead of replacing the defaults
 *     (a passing `headers` object can no longer drop Authorization),
 *  3. failures surface as a typed ApiError carrying status + backend code.
 *
 * No network: `fetch` is stubbed per test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE, apiFetch, ApiError, setToken } from "./api";

/** Minimal Response stand-in — apiFetch only reads `ok`, `status`, `json()`. */
function responseWith(status: number, json: () => Promise<unknown>): Response {
  return { ok: status >= 200 && status < 300, status, json } as unknown as Response;
}

function jsonResponse(status: number, body: unknown): Response {
  return responseWith(status, async () => body);
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  localStorage.clear();
  fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function lastCall(): { url: unknown; init: RequestInit | undefined } {
  expect(fetchMock).toHaveBeenCalled();
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [
    unknown,
    RequestInit | undefined,
  ];
  return { url, init };
}

describe("apiFetch", () => {
  it("adds Authorization when a token exists", async () => {
    setToken("token-abc");
    const body = await apiFetch<{ ok: boolean }>("/auth/me");
    expect(body).toEqual({ ok: true });

    const { url, init } = lastCall();
    expect(url).toBe(`${API_BASE}/auth/me`);
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token-abc");
    expect(headers["Content-Type"]).toBe("application/json");
  });

  it("sends no Authorization header for the anonymous demo session", async () => {
    setToken(null);
    await apiFetch("/programs");
    const headers = lastCall().init?.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(headers["Content-Type"]).toBe("application/json");
  });

  it("merges init.headers without dropping auth (auth wins over a stale caller token)", async () => {
    setToken("token-abc");
    await apiFetch("/research/runs", {
      method: "POST",
      body: "{}",
      headers: { "X-Trace": "trace-1", Authorization: "Bearer stale-caller-token" },
    });

    const headers = lastCall().init?.headers as Record<string, string>;
    expect(headers["X-Trace"]).toBe("trace-1"); // caller header survives
    expect(headers["Content-Type"]).toBe("application/json"); // default survives
    expect(headers.Authorization).toBe("Bearer token-abc"); // stored token wins
  });

  it("surfaces a 4xx as ApiError carrying status and backend code", async () => {
    fetchMock.mockImplementationOnce(async () =>
      jsonResponse(404, { error: { code: "NOT_FOUND", message: "Program not found" } })
    );

    const err = await apiFetch("/programs/nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const api = err as ApiError;
    expect(api.name).toBe("ApiError");
    expect(api.status).toBe(404);
    expect(api.code).toBe("NOT_FOUND");
    expect(api.message).toBe("Program not found");
  });

  it("turns a 401 without a stored token into a plain ApiError (anonymous caller)", async () => {
    setToken(null);
    fetchMock.mockImplementationOnce(async () =>
      jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "Authentication required" } })
    );

    const err = await apiFetch("/auth/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).code).toBe("UNAUTHENTICATED");
  });

  it("times out a request that never settles instead of loading forever", async () => {
    // Regression: a browser holding keep-alive sockets to a restarted backend
    // left /onboarding on its spinner indefinitely — the fetch never resolved
    // and never rejected. The client must abort and surface a typed TIMEOUT.
    vi.useFakeTimers();
    try {
      // Faithful to real fetch: reject when the abort signal fires.
      fetchMock.mockImplementationOnce(
        (_url: unknown, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted.", "AbortError"))
            );
          })
      );
      const pending = apiFetch("/onboarding/schema").catch((e: unknown) => e);
      await vi.advanceTimersByTimeAsync(20_000);
      const err = await pending;
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).code).toBe("TIMEOUT");
      expect((err as ApiError).status).toBe(0);
      expect((err as ApiError).message).toContain("did not answer within 20s");
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    fetchMock.mockImplementationOnce(async () =>
      responseWith(502, async () => {
        throw new Error("not json");
      })
    );

    const err = await apiFetch("/evidence/x/recheck").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("Request failed (502)");
    expect((err as ApiError).code).toBeNull();
  });
});
