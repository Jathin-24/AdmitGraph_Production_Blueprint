"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../lib/auth";
import { AccountMenu } from "./account";
import { NotificationsBell } from "./notifications-bell";

const PRIMARY_ITEMS = [
  { href: "/explore", label: "Explore" },
  { href: "/dashboard", label: "My Plan" },
  { href: "/monitor", label: "Monitor" },
  { href: "/profile", label: "Profile" },
];

// Second-tier destinations (W12-W15 wave). They join the mobile panel as
// flat links, but the desktop row can't hold 7 labels between md and lg,
// so on desktop they live behind the "More" disclosure below.
const MORE_ITEMS = [
  { href: "/applications", label: "Applications" },
  { href: "/scholarships", label: "Scholarships" },
  { href: "/visa", label: "Visa & funding" },
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
 * Desktop-only "More" disclosure holding the second-tier links
 * (Applications / Scholarships / Visa). Mirrors AccountMenu's popover
 * contract: aria-haspopup/aria-expanded, click-outside and Escape close it.
 */
function MoreMenu({ pathname }: { pathname: string }) {
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

  const active = MORE_ITEMS.some((item) => pathname.startsWith(item.href));

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className={`-mb-px border-b-2 px-3 py-4 text-sm transition-colors ${
          active
            ? "border-forest font-medium text-ink"
            : "border-transparent text-ink-soft hover:text-ink"
        }`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More pages"
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        More
        <span aria-hidden className="ml-1 text-[9px]">
          ▼
        </span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="More pages"
          className="card absolute left-0 top-full z-30 mt-1 w-52 p-2 text-left"
        >
          {MORE_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              role="menuitem"
              className="block rounded-md px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-paper-dark hover:text-ink"
              onClick={() => setOpen(false)}
            >
              {item.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * P2-18 responsive nav: the desktop row (logo + links + CTA + bell +
 * account) overflows once seven destinations exist, so the inline row
 * appears only from `lg` up (4 primary links + a "More" disclosure for
 * Applications/Scholarships/Visa); below `lg` the links and CTA live in a
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

        {/* Desktop links — hidden below lg, replaced by the panel below. */}
        <div className="hidden flex-1 items-center gap-1 lg:flex">
          {PRIMARY_ITEMS.map((item) => {
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
          <MoreMenu pathname={pathname} />
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
          className="-m-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-ink-soft transition-colors hover:bg-paper-dark hover:text-ink lg:hidden"
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

      {/* Mobile panel: links + CTA. `hidden` collapses it; `lg:hidden` keeps
          it off desktop even if state gets stuck open. */}
      <div id="nav-mobile-menu" hidden={!menuOpen} className="border-t border-line lg:hidden">
        <div className="mx-auto flex max-w-5xl flex-col gap-1 px-4 py-3">
          {[...PRIMARY_ITEMS, ...MORE_ITEMS].map((item) => {
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
