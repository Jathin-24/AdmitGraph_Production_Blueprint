"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

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

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-paper/95 backdrop-blur">
      <nav
        className="mx-auto flex h-14 max-w-5xl items-center gap-6 px-5"
        aria-label="Main navigation"
      >
        <Link
          href="/"
          className="flex items-center gap-2 text-ink"
          aria-label="AdmitGraph home"
        >
          <Mark />
          <span className="display text-lg font-medium tracking-tight">AdmitGraph</span>
        </Link>

        <div className="flex flex-1 items-center gap-1">
          {ITEMS.map((item) => {
            const active = pathname.startsWith(item.href);
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

        <Link href="/research" className="btn-primary btn-sm">
          Build my strategy
        </Link>
      </nav>
    </header>
  );
}
