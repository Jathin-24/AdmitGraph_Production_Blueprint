/**
 * P2-24 source-scan regression tests — the frontend mirror of the backend's
 * `tests/test_frontend_secrets.py`.
 *
 * 1. No bare `fetch(` outside `lib/api.ts` / `lib/api-extra.ts` unless the
 *    file is server-only (no "use client"): a client-side bare fetch skips
 *    the Authorization header, so a signed-in user silently reads the
 *    anonymous demo profile (audit P0-3).
 * 2. No credential-shaped literals anywhere in frontend sources.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Vitest runs with the frontend package root as cwd (npm test / CI);
// fall back to <repo>/frontend/app when launched from the repo root.
const APP_ROOT = [path.resolve(process.cwd(), "app"), path.resolve(process.cwd(), "frontend/app")].find(
  (candidate) => existsSync(path.join(candidate, "lib", "api.ts"))
) as string;

/** The only two modules allowed to talk to `fetch` directly (both attach auth). */
const FETCH_ALLOWLIST = new Set(["lib/api.ts", "lib/api-extra.ts"]);

function collectFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectFiles(full));
    else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/** Strip comments so prose like "never call bare fetch()" is not a match. */
function stripComments(source: string): string {
  let out = "";
  let i = 0;
  let inString: string | null = null;
  let inLine = false;
  let inBlock = false;
  while (i < source.length) {
    const ch = source[i];
    const next = source[i + 1];
    if (inLine) {
      if (ch === "\n") {
        inLine = false;
        out += ch;
      }
      i += 1;
      continue;
    }
    if (inBlock) {
      if (ch === "*" && next === "/") {
        inBlock = false;
        i += 2;
        continue;
      }
      if (ch === "\n") out += ch;
      i += 1;
      continue;
    }
    if (inString) {
      out += ch;
      if (ch === "\\") {
        out += next ?? "";
        i += 2;
        continue;
      }
      if (ch === inString) inString = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      inString = ch;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === "/" && next === "/") {
      inLine = true;
      i += 2;
      continue;
    }
    if (ch === "/" && next === "*") {
      inBlock = true;
      i += 2;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

const SECRET_PATTERNS: { label: string; re: RegExp }[] = [
  { label: "private key block", re: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { label: "AWS access key id", re: /\bAKIA[0-9A-Z]{16}\b/ },
  { label: "GitHub token", re: /\bgh[pousr]_[A-Za-z0-9]{30,}\b/ },
  { label: "Slack token", re: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/ },
  { label: "OpenAI-style key", re: /\bsk-[A-Za-z0-9_-]{24,}\b/ },
  {
    label: "hardcoded credential assignment",
    re: /\b(password|passwd|secret|api[_-]?key|jwt[_-]?secret|token)\b\s*[:=]\s*["'][^"'\s]{12,}["']/i,
  },
];

describe("frontend source scan", () => {
  const files = collectFiles(APP_ROOT);
  const sources = new Map(
    files.map((f) => [path.relative(APP_ROOT, f).replaceAll("\\", "/"), readFileSync(f, "utf8")])
  );

  it("found a non-trivial number of source files (scan is not vacuous)", () => {
    expect(files.length).toBeGreaterThan(20);
    expect(sources.has("lib/api.ts")).toBe(true);
  });

  it("allows no bare fetch( outside lib/api.ts + lib/api-extra.ts in client code", () => {
    const offenders: string[] = [];
    for (const [rel, raw] of Array.from(sources)) {
      if (FETCH_ALLOWLIST.has(rel)) continue;
      const code = stripComments(raw);
      if (!/(?<![.\w])fetch\s*\(/.test(code)) continue;
      // Server-only modules (no "use client") run where there is no browser
      // session to leak; today that is programs/[id]/page.tsx's metadata
      // prefetch of the public catalog endpoint.
      if (!code.includes('"use client"')) continue;
      offenders.push(rel);
    }
    expect(offenders, `bare fetch( in client code — use apiFetch from lib/api.ts`).toEqual([]);
  });

  it("documents every module that calls fetch directly", () => {
    // Allowlist can only shrink: each entry must actually use fetch.
    for (const rel of Array.from(FETCH_ALLOWLIST)) {
      expect(sources.has(rel), `${rel} missing from app/`).toBe(true);
      const code = stripComments(sources.get(rel) as string);
      expect(code, `${rel} no longer calls fetch — remove it from the allowlist`).toMatch(
        /(?<![.\w])fetch\s*\(/
      );
    }
  });

  it("contains no credential-shaped literals", () => {
    const findings: string[] = [];
    for (const [rel, raw] of Array.from(sources)) {
      for (const { label, re } of SECRET_PATTERNS) {
        if (re.test(raw)) findings.push(`${rel}: ${label}`);
      }
    }
    expect(findings, `secret-like material in frontend source:\n${findings.join("\n")}`).toEqual([]);
  });
});
