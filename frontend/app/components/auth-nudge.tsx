"use client";

import Link from "next/link";

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
