"use client";

import { useEffect, useState } from "react";

/**
 * FRONTEND_SPEC §Program detail — the nine sections pile up into a long
 * scroll, so this is a sticky "on this page" nav.
 *
 * Behaviour:
 * - anchors point at each section's `id`; a click scrolls there smoothly
 *   ONLY when the visitor allows motion (`prefers-reduced-motion` ⇒ instant),
 *   then moves focus to the section so keyboard/screen-reader users keep
 *   their place;
 * - an IntersectionObserver (disconnected on unmount) tracks which section
 *   sits under the sticky bars and marks it `aria-current="location"`;
 * - on narrow screens the row simply scrolls horizontally — no dropdown,
 *   same links, same tab order.
 */
export interface SectionLink {
  id: string;
  label: string;
}

/** Site header is `h-14` (56px) and sticky; leave a little air below it. */
const STICKY_OFFSET = 64;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function SectionNav({ sections }: { sections: SectionLink[] }) {
  const [activeId, setActiveId] = useState<string>(sections[0]?.id ?? "");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || sections.length === 0) return;

    // Sections currently overlapping the observation band. Kept as elements
    // so rects are re-read fresh on every decision (never stale).
    const visible = new Set<Element>();

    const atBottom = () => {
      const doc = document.documentElement;
      // Nothing to scroll ⇒ the first section is the current one.
      if (doc.scrollHeight <= window.innerHeight + 8) return false;
      return window.innerHeight + window.scrollY >= doc.scrollHeight - 4;
    };

    const pick = () => {
      if (visible.size === 0) return; // gap between cards — keep last answer
      if (atBottom()) {
        setActiveId(sections[sections.length - 1].id);
        return;
      }
      // Snapshot into an array first so plain control-flow narrowing applies.
      const candidates: Element[] = [];
      visible.forEach((el) => candidates.push(el));

      let passed: Element | null = null;
      let upcoming: Element | null = null;
      for (const el of candidates) {
        const top = el.getBoundingClientRect().top;
        if (top <= STICKY_OFFSET) {
          // Last section whose heading has scrolled past the sticky bars.
          if (!passed || top > passed.getBoundingClientRect().top) passed = el;
        } else if (!upcoming || top < upcoming.getBoundingClientRect().top) {
          upcoming = el;
        }
      }
      const chosen = passed ?? upcoming;
      if (chosen) setActiveId(chosen.id);
    };

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target);
          else visible.delete(entry.target);
        }
        pick();
      },
      { rootMargin: `-${STICKY_OFFSET}px 0px -55% 0px`, threshold: 0 }
    );

    for (const section of sections) {
      const el = document.getElementById(section.id);
      if (el) observer.observe(el);
    }

    // Re-evaluate when the page bottom comes into reach without any section
    // crossing the band (short last section).
    const onScroll = () => pick();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [sections]);

  function goTo(event: React.MouseEvent<HTMLAnchorElement>, id: string) {
    const target = document.getElementById(id);
    if (!target) return; // fall back to the native anchor jump
    event.preventDefault();
    target.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });
    target.focus({ preventScroll: true });
    setActiveId(id);
    if (window.history.replaceState) {
      // Preserve Next's own history state object — only the hash changes.
      window.history.replaceState(window.history.state, "", `#${id}`);
    }
  }

  return (
    <nav
      aria-label="On this page"
      className="sticky top-14 z-10 -mx-5 border-b border-line bg-paper/95 px-5 backdrop-blur"
    >
      <ol className="flex overflow-x-auto">
        {sections.map((section) => {
          const active = section.id === activeId;
          return (
            <li key={section.id} className="shrink-0">
              <a
                href={`#${section.id}`}
                onClick={(event) => goTo(event, section.id)}
                aria-current={active ? "location" : undefined}
                className={`-mb-px inline-block whitespace-nowrap border-b-2 px-3 py-3 text-[13px] transition-colors ${
                  active
                    ? "border-forest font-medium text-ink"
                    : "border-transparent text-ink-soft hover:text-ink"
                }`}
              >
                {section.label}
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Anchor wrapper around one spec section: carries the `id` the nav links
 *  to, a scroll margin so the sticky bars never cover the heading, and
 *  `tabIndex={-1}` so the nav can move focus here after a jump. */
export function SectionAnchor({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <div id={id} tabIndex={-1} className="scroll-mt-32">
      {children}
    </div>
  );
}
