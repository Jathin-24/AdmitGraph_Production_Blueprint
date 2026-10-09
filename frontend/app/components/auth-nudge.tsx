"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "../lib/auth";

/**
 * Non-blocking nudge for anonymous visitors on pages where their work would
 * be saved to the demo profile instead of their own account. Renders nothing
 * once signed in — never gates access, only explains the stakes.
 *
 * `next` is the current path; register/login return to it after auth.
 */
export function AuthNudge({
  next,
  children,
}: {
  next: string;
  children?: React.ReactNode;
}) {
  const { status } = useAuth();
  if (status !== "anonymous") return null;
  const encoded = encodeURIComponent(next);

  return (
    <div className="card border border-dashed p-4" role="note">
      <p className="text-sm text-ink-soft">
        {children ??
          "You're browsing as a guest — anything you build here is saved to the demo, not to you."}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Link href={`/register?next=${encoded}`} className="btn-primary btn-sm">
          Create a free account
        </Link>
        <Link href={`/login?next=${encoded}`} className="link text-sm">
          I already have an account
        </Link>
      </div>
    </div>
  );
}

export default AuthNudge;

/* --------------------------------------------------------- demo banner */

/** localStorage key for the demo-banner dismissal (persisted so the notice
 *  doesn't reappear on every navigation, but reappears in a new browser). */
const DEMO_BANNER_KEY = "admitgraph_demo_banner_dismissed";

/** Routes where guest edits land on the shared demo profile. */
const GUEST_ROUTES = ["/dashboard", "/profile", "/research", "/onboarding"];

/**
 * Site-wide guest-mode disclosure (P2-21 hardening + P0-3 honesty): mounted
 * once in the root layout, it self-filters to the routes where an anonymous
 * visitor's data would be written to the shared demo profile. Renders
 * nothing for signed-in users (AuthContext) and nothing until hydration
 * completes, so server and client markup always agree. Dismissal persists
 * in localStorage.
 */
export function DemoModeBanner() {
  const { status } = useAuth();
  const pathname = usePathname();
  const [dismissed, setDismissed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(DEMO_BANNER_KEY) === "1");
    } catch {
      /* storage unavailable (private mode) — banner simply shows */
    }
    setHydrated(true);
  }, []);

  if (!hydrated || status !== "anonymous") return null;
  const onGuestRoute = GUEST_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );
  if (!onGuestRoute || dismissed) return null;

  function dismiss() {
    setDismissed(true);
    try {
      localStorage.setItem(DEMO_BANNER_KEY, "1");
    } catch {
      /* ignore */
    }
  }

  return (
    <div
      role="note"
      aria-label="Guest demo mode"
      className="border-b border-line bg-amberx-tint"
    >
      <div className="mx-auto flex max-w-5xl items-start gap-3 px-5 py-2.5">
        <p className="flex-1 text-sm text-ink-soft">
          You&apos;re viewing the shared demo profile — data you enter here is public demo
          data.{" "}
          <Link href="/register" className="link font-medium text-ink">
            Sign up to save your own
          </Link>
          .
        </p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss guest demo notice"
          className="shrink-0 rounded p-1 text-ink-faint transition-colors hover:bg-paper-dark hover:text-ink"
        >
          <span aria-hidden>✕</span>
        </button>
      </div>
    </div>
  );
}
