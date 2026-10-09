"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../lib/auth";

/**
 * Session control for the nav: "Sign in" link when anonymous, otherwise a
 * small account popover (email + name + sign out).
 *
 * Reads the global AuthProvider rather than fetching /auth/me itself — the
 * nav lives in the root layout, which Next.js never remounts on client-side
 * navigation, so a local fetch would stay stale ("still says Sign in after
 * logging in") until a full page reload.
 */
export function AccountMenu() {
  const router = useRouter();
  const { user, status, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function handleSignOut(): void {
    signOut();
    setOpen(false);
    router.push("/");
  }

  // Session still resolving: keep the header slot empty rather than flashing
  // the wrong control.
  if (status === "loading") return null;

  // Anonymous: one compact "Guest" control instead of two competing links —
  // it makes the demo state visible in the account menu (P2-21/P0-3) and
  // keeps the header from overflowing at 360px (P2-18). The menu states
  // plainly that guest data is public demo data, then offers both doors.
  if (user === null) {
    return (
      <div className="relative" ref={containerRef}>
        <button
          type="button"
          className="btn-ghost btn-sm"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="Guest demo mode — account options"
          onClick={() => setOpen((wasOpen) => !wasOpen)}
        >
          Guest
          <span aria-hidden className="text-[9px]">
            ▼
          </span>
        </button>

        {open && (
          <div
            role="menu"
            aria-label="Guest demo mode"
            className="card absolute right-0 top-full z-30 mt-1 w-72 p-4 text-left"
          >
            <p className="text-sm font-medium text-ink">You&apos;re in guest mode</p>
            <p className="hint mt-1">
              You&apos;re viewing the shared demo profile — data you enter here is public demo
              data.
            </p>
            <div className="my-3 border-t border-line" />
            <Link
              href="/register"
              role="menuitem"
              className="btn-primary btn-sm w-full"
              onClick={() => setOpen(false)}
            >
              Sign up to save your own
            </Link>
            <Link
              href="/login"
              role="menuitem"
              className="btn-ghost btn-sm mt-2 w-full"
              onClick={() => setOpen(false)}
            >
              I already have an account
            </Link>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="btn-ghost btn-sm max-w-[6.5rem] truncate md:max-w-[10rem]"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        {user.full_name || user.email}
        <span aria-hidden className="text-[9px]">
          ▼
        </span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="card absolute right-0 top-full z-30 mt-1 w-64 p-4 text-left"
        >
          <p className="truncate text-sm font-medium text-ink">
            {user.full_name ?? "Your account"}
          </p>
          <p className="hint mt-0.5 truncate">{user.email}</p>
          <p className="mt-2">
            <span className="chip chip-neutral">{user.role}</span>
          </p>
          <div className="my-3 border-t border-line" />
          {/* The only entry point to /admin: the backend enforces ADMIN on
              every route (P0-2), but the link itself must never tempt a
              student into a guaranteed 403. Role comes from GET /auth/me
              via AuthUser.role (parsed in schemas.ts meResponseSchema). */}
          {user.role === "ADMIN" && (
            <Link
              href="/admin"
              role="menuitem"
              className="btn-ghost btn-sm mb-1 w-full justify-start"
              onClick={() => setOpen(false)}
            >
              Admin
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            className="btn-ghost btn-sm w-full justify-start"
            onClick={handleSignOut}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default AccountMenu;
