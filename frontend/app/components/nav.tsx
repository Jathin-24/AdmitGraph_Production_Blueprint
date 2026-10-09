"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../lib/auth";
import { AccountMenu } from "./account";
import { NotificationsBell } from "./notifications-bell";

const ITEMS = [
  { href: "/explore", label: "Explore" },
  { href: "/dashboard", label: "My Plan" },
  { href: "/monitor", label: "Monitor" },
  { href: "/profile", label: "Profile" },
];

function Mark() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="h-5 w-5 text-forest"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <circle cx="5" cy="18" r="2.4" />
      <circle cx="12" cy="6" r="2.4" />
      <circle cx="19" cy="15" r="2.4" />
      <path d="M6.8 16.4 10.4 8M13.6 7.4 17.4 13.2M7 18h9.8" strokeLinecap="round" />
    </svg>
  );
}

/**
 * P2-18 responsive nav: the desktop row (logo + 4 links + CTA + bell +
 * account) overflows at 360px, so below `md` the links and CTA live in a
 * disclosure panel behind a hamburger button (aria-expanded/aria-controls,
 * Escape closes and returns focus to the toggle, route changes close it).
 * Bell + account stay in the header at every width — notifications and
 * session controls never hide behind a menu.
 */
export function Nav() {
  const pathname = usePathname();
  const { status } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  // Escape closes the panel and hands focus back to the button that opened it.
  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
        toggleRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  // Navigating (links inside the panel, or any client-side route change)
  // closes the panel so it never lingers over the new page.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  const isAuthed = status === "authenticated";

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-paper/95 backdrop-blur">
      <nav
        className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-4 md:gap-6 md:px-5"
        aria-label="Main navigation"
      >
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-ink"
          aria-label="AdmitGraph home"
        >
          <Mark />
          <span className="display text-base font-medium tracking-tight md:text-lg">
            AdmitGraph
          </span>
        </Link>

        {/* Desktop links — hidden below md, replaced by the panel below. */}
        <div className="hidden flex-1 items-center gap-1 md:flex">
          {ITEMS.map((item) => {
            const active =
              item.href === "/explore"
                ? pathname.startsWith("/explore") || pathname.startsWith("/programs")
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`-mb-px border-b-2 px-3 py-4 text-sm transition-colors ${
                  active
                    ? "border-forest font-medium text-ink"
                    : "border-transparent text-ink-soft hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </div>

        <div className="ml-auto flex items-center gap-3">
          {/* Session-aware actions. A signed-in student resumes their plan;
              an anonymous visitor gets one unmistakable way in. Both used to
              show the same "Build my strategy" regardless of state. The CTA
              is desktop-only here — it lives in the mobile panel too, where
              it has room to breathe. */}
          {isAuthed ? (
            <Link href="/research" className="btn-primary btn-sm hidden md:inline-flex">
              Build my strategy
            </Link>
          ) : (
            <Link href="/research" className="btn-ghost btn-sm hidden md:inline-flex">
              See an example
            </Link>
          )}
          <NotificationsBell />
          <AccountMenu />
        </div>

        {/* Mobile disclosure toggle (P2-18). */}
        <button
          ref={toggleRef}
          type="button"
          className="-m-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-ink-soft transition-colors hover:bg-paper-dark hover:text-ink md:hidden"
          aria-expanded={menuOpen}
          aria-controls="nav-mobile-menu"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            {menuOpen ? (
              <path d="M6 6l12 12M18 6L6 18" />
            ) : (
              <path d="M4 7h16M4 12h16M4 17h16" />
            )}
          </svg>
        </button>
      </nav>

      {/* Mobile panel: links + CTA. `hidden` collapses it; `md:hidden` keeps
          it off desktop even if state gets stuck open. */}
      <div id="nav-mobile-menu" hidden={!menuOpen} className="border-t border-line md:hidden">
        <div className="mx-auto flex max-w-5xl flex-col gap-1 px-4 py-3">
          {ITEMS.map((item) => {
            const active =
              item.href === "/explore"
                ? pathname.startsWith("/explore") || pathname.startsWith("/programs")
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-forest-tint font-medium text-ink"
                    : "text-ink-soft hover:bg-paper-dark hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <Link
            href="/research"
            className={`btn-sm mt-2 justify-center ${isAuthed ? "btn-primary" : "btn-ghost"}`}
          >
            {isAuthed ? "Build my strategy" : "See an example"}
          </Link>
        </div>
      </div>
    </header>
  );
}
