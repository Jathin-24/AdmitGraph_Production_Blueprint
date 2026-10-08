"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { getMe, getToken, setToken, type AuthUser } from "../lib/api";

/**
 * Session control for the nav: "Sign in" link when anonymous, otherwise a
 * small account popover (email + name + sign out).
 *
 * Token state is only read inside effects, so server and client render the
 * same markup on first paint (no localStorage hydration mismatch).
 */
export function AccountMenu() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (getToken() === null) {
      setReady(true);
      return () => {
        cancelled = true;
      };
    }
    getMe()
      .then((response) => {
        if (!cancelled) setUser(response.user);
      })
      .catch(() => {
        // apiFetch already dropped the invalid token on 401.
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  function signOut(): void {
    setToken(null);
    setUser(null);
    setOpen(false);
    router.push("/");
  }

  if (!ready) return null;

  if (user === null) {
    return (
      <Link href="/login" className="btn-ghost btn-sm">
        Sign in
      </Link>
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
            onClick={signOut}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default AccountMenu;
