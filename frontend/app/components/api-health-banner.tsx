"use client";

import { useEffect, useState } from "react";

import { API_BASE, checkApiHealth } from "../lib/api";

type Status = "checking" | "ok" | "unreachable" | "wrong";

/**
 * Startup self-check for the API base URL (see API_BASE in lib/api.ts).
 *
 * `NEXT_PUBLIC_API_BASE_URL` is inlined at build time, so a production build
 * made without it falls back to localhost — every query would then fail with
 * a generic "Cannot reach the server". This banner runs one lightweight
 * GET {API_BASE}/health on mount and, when it does not come back healthy,
 * says exactly which base URL the app is talking to and what to set.
 * Dismissing it lasts for the session; a Retry re-checks.
 */
export function ApiHealthBanner() {
  const [status, setStatus] = useState<Status>("checking");
  const [dismissed, setDismissed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus("checking");
    checkApiHealth().then((httpStatus) => {
      if (cancelled) return;
      if (httpStatus !== null && httpStatus >= 200 && httpStatus < 300) setStatus("ok");
      else if (httpStatus === null) setStatus("unreachable");
      else setStatus("wrong");
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (dismissed || status === "checking" || status === "ok") return null;

  const message =
    status === "unreachable"
      ? `Cannot reach the API server at ${API_BASE} — check NEXT_PUBLIC_API_BASE_URL.`
      : `The API server at ${API_BASE} answered /health with an error — check NEXT_PUBLIC_API_BASE_URL.`;

  return (
    <div role="alert" className="border-b border-line bg-danger-tint">
      <div className="mx-auto flex max-w-5xl items-start gap-3 px-5 py-2.5">
        <p className="flex-1 text-sm text-danger">{message}</p>
        <button
          type="button"
          onClick={() => setAttempt((n) => n + 1)}
          className="link shrink-0 text-sm font-medium text-danger"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss API connection warning"
          className="shrink-0 rounded p-1 text-danger/70 transition-colors hover:bg-danger/10 hover:text-danger"
        >
          <span aria-hidden>✕</span>
        </button>
      </div>
    </div>
  );
}

export default ApiHealthBanner;
