/**
 * P2-24 — schema parse policy (schemas.ts file header):
 *  - STRICT parsers (collections pages `.map()` over) throw ParseError on drift,
 *  - LENIENT parsers fall back to the raw payload instead of white-screening,
 *  - ParseError is typed so react-query's retry policy can single it out.
 */

import { describe, expect, it, vi } from "vitest";

import {
  authResponseSchema,
  notificationsResponseSchema,
  ParseError,
  parseWith,
  strictParser,
} from "./schemas";
import { z } from "zod";

describe("strictParser + parseWith", () => {
  it("returns parsed data when a strict collection matches", () => {
    const out = parseWith({ items: [], unread_count: 3 }, notificationsResponseSchema);
    expect(out).toEqual({ items: [], unread_count: 3 });
  });

  it("throws a typed ParseError on collection drift (the .map() crash guard)", () => {
    // `items` is not an array — exactly the drift that used to explode on `.map()`.
    expect(() => parseWith({ items: "nope" }, notificationsResponseSchema)).toThrow(ParseError);

    let caught: unknown;
    try {
      parseWith({ items: [{ id: 1 }] }, notificationsResponseSchema);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ParseError);
    const parseError = caught as ParseError;
    expect(parseError.name).toBe("ParseError");
    expect(parseError.message).toBe("Response did not match the expected shape.");
    expect(parseError.zodError).toBeDefined(); // carries the zod issue list for debugging
  });

  it("marks strict parsers so api.ts can tell them apart structurally", () => {
    expect(strictParser(z.object({ a: z.string() })).strictParse).toBe(true);
    const lenient = authResponseSchema as unknown as { strictParse?: boolean };
    expect(lenient.strictParse).toBeUndefined();
  });

  it("falls back to the raw payload for a lenient parser (dev warning, no throw)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const raw = { token: 123, user: null }; // wrong types on purpose
    const out = parseWith(raw, authResponseSchema);
    expect(out).toBe(raw); // rendered as-is, never a white screen
    warn.mockRestore();
  });

  it("lenient parser passes valid data through", () => {
    const raw = { token: "t", user: { id: "1", email: "a@b.c", full_name: null, role: "STUDENT" } };
    expect(parseWith(raw, authResponseSchema)).toEqual(raw);
  });
});
