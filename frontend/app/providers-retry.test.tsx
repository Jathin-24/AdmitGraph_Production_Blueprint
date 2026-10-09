/**
 * P2-24 — react-query retry policy (Providers).
 *
 * The decision function lives inside Providers' QueryClient defaultOptions,
 * so this probe reads it back off the client and exercises it directly:
 *  - schema ParseError: never retry (a retry cannot fix a shape mismatch),
 *  - 4xx ApiError: never retry (the request itself is wrong),
 *  - 5xx / network: one retry, then give up.
 */

import { useQueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "./lib/api";
import { ParseError } from "./lib/schemas";
import { Providers } from "./providers";

type RetryFn = (failureCount: number, error: Error) => boolean;

let retry: RetryFn | undefined;

function Probe() {
  const client = useQueryClient();
  retry = client.getDefaultOptions().queries?.retry as RetryFn | undefined;
  return null;
}

function getRetry(): RetryFn {
  render(
    <Providers>
      <Probe />
    </Providers>
  );
  expect(retry).toBeTypeOf("function");
  return retry as RetryFn;
}

describe("Providers query retry policy", () => {
  it("never retries a schema ParseError", () => {
    const decide = getRetry();
    expect(decide(0, new ParseError("Response did not match the expected shape."))).toBe(false);
  });

  it("never retries a 4xx ApiError (404, 429, …)", () => {
    const decide = getRetry();
    expect(decide(0, new ApiError("not found", 404, "NOT_FOUND"))).toBe(false);
    expect(decide(0, new ApiError("slow down", 429, "RATE_LIMITED"))).toBe(false);
    expect(decide(1, new ApiError("bad request", 400, "VALIDATION_ERROR"))).toBe(false);
  });

  it("retries a 5xx / network failure exactly once", () => {
    const decide = getRetry();
    expect(decide(0, new ApiError("upstream", 502, "BAD_GATEWAY"))).toBe(true);
    expect(decide(1, new ApiError("upstream", 502, "BAD_GATEWAY"))).toBe(false);
    expect(decide(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(decide(1, new TypeError("Failed to fetch"))).toBe(false);
  });
});
