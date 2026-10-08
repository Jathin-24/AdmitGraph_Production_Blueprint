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

  if (user === null) {
    return (
      <>
        <Link href="/login" className="btn-ghost btn-sm">
          Sign in
        </Link>
        <Link href="/register" className="btn-primary btn-sm">
          Get started free
        </Link>
      </>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="btn-ghost btn-sm max-w-[10rem] truncate"
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
