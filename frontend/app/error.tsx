"use client";

import Link from "next/link";
import { useEffect } from "react";

/**
 * Route error boundary (P2-19). Shows a friendly, actionable message —
 * never a stack trace in production (dev additionally gets the message so
 * local debugging stays possible). `reset()` re-renders the segment.
 */
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface the full error in the console for debugging — consoles are
    // dev/ops visibility, the rendered page stays clean.
    console.error("[route-error]", error);
  }, [error]);

  const isDev = process.env.NODE_ENV !== "production";

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 px-5 py-16">
      <p className="eyebrow">Something went wrong</p>
      <h1 className="display text-2xl font-medium leading-tight text-ink">
        This page hit an unexpected error.
      </h1>
      <p className="text-sm text-ink-soft">
        Nothing you entered was lost — your data stays on the server. Try again, and if it
        keeps happening go back home and take another route in.
      </p>

      {isDev && (
        <pre className="overflow-x-auto rounded-lg border border-danger/30 bg-danger-tint px-4 py-3 text-xs text-danger">
          {error.message}
          {error.digest ? ` (${error.digest})` : ""}
        </pre>
      )}
      {!isDev && error.digest && (
        <p className="text-xs text-ink-faint">Reference: {error.digest}</p>
      )}

      <div className="mt-2 flex flex-wrap gap-3">
        <button type="button" className="btn-primary" onClick={() => reset()}>
          Try again
        </button>
        <Link href="/" className="btn-secondary">
          Back home
        </Link>
        <Link href="/explore" className="btn-ghost">
          Explore programs
        </Link>
      </div>
    </main>
  );
}
