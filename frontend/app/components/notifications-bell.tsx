"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "../lib/api";
import { fmtDate } from "./ui";

/** Tone + label for every notification type we know about. Unknown types fall
 *  back to a neutral chip with a humanised label — never colour-only meaning. */
export type NotificationTone = "good" | "warn" | "bad" | "neutral";

export const NOTIFICATION_META: Record<string, { label: string; tone: NotificationTone }> = {
  WELCOME: { label: "Welcome", tone: "good" },
  RESEARCH_COMPLETE: { label: "Research complete", tone: "good" },
  SENT: { label: "Sent", tone: "good" },
  DEADLINE_CHANGED: { label: "Deadline changed", tone: "warn" },
  REQUIREMENT_CHANGED: { label: "Requirement changed", tone: "warn" },
  SCHOLARSHIP_SIGNAL: { label: "New scholarship signal", tone: "good" },
  CONFLICT_DETECTED: { label: "Conflicting information", tone: "bad" },
  SOURCE_STALE: { label: "Source became stale", tone: "bad" },
};

export function notificationMeta(type: string): { label: string; tone: NotificationTone } {
  const known = NOTIFICATION_META[type];
  if (known) return known;
  const human = type
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { label: human || "Notification", tone: "neutral" };
}

export const TONE_CHIP: Record<NotificationTone, string> = {
  good: "chip-good",
  warn: "chip-warn",
  bad: "chip-bad",
  neutral: "chip-neutral",
};

function BellIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7" />
      <path d="M10.3 20a2 2 0 0 0 3.4 0" />
    </svg>
  );
}

function itemButtonClasses(unread: boolean): string {
  return [
    "flex w-full items-start gap-2.5 border-b border-line px-3 py-2.5 text-left",
    "transition-colors hover:bg-paper-dark/70",
    unread ? "bg-forest-tint/40" : "",
  ].join(" ");
}

export function NotificationsBell() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: listNotifications,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });

  // Close the dropdown on outside click or Escape (keyboard accessible).
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const markOne = useMutation({
    mutationFn: (item: NotificationItem) =>
      item.read ? Promise.resolve({ read: true }) : markNotificationRead(item.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const markAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const items = notifications.data?.items ?? [];
  const unread = notifications.data?.unread_count ?? 0;

  function openItem(item: NotificationItem) {
    if (!item.read) markOne.mutate(item);
    setOpen(false);
    if (item.link) router.push(item.link);
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="true"
        aria-expanded={open}
        className="relative -m-1.5 flex h-8 w-8 items-center justify-center rounded-md text-ink-soft transition-colors hover:bg-paper-dark hover:text-ink"
      >
        <BellIcon className="h-5 w-5" />
        {unread > 0 && (
          <span
            aria-hidden
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold leading-none text-white"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-10 z-30 w-80 overflow-hidden rounded-lg border border-line bg-white shadow-card"
          aria-label="Notifications"
        >
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Notifications
            </span>
            <button
              type="button"
              onClick={() => markAll.mutate()}
              disabled={unread === 0 || markAll.isPending}
              className="link text-xs text-ink-soft"
            >
              Mark all read
            </button>
          </div>

          {notifications.isLoading && (
            <p className="px-3 py-4 text-xs text-ink-faint" role="status">
              Loading notifications…
            </p>
          )}

          {notifications.isError && (
            <p className="px-3 py-4 text-xs text-ink-faint" role="status">
              Notifications are unavailable right now — everything else still works.
            </p>
          )}

          {notifications.isSuccess && items.length === 0 && (
            <p className="px-3 py-4 text-sm text-ink-faint">You&apos;re all caught up.</p>
          )}

          <ul className="max-h-80 overflow-y-auto">
            {items.slice(0, 8).map((item) => {
              const meta = notificationMeta(item.type);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => openItem(item)}
                    className={itemButtonClasses(!item.read)}
                  >
                    <span className={`chip mt-0.5 shrink-0 ${TONE_CHIP[meta.tone]}`}>
                      {meta.label}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-ink">
                        {item.title}
                      </span>
                      <span className="mt-0.5 block line-clamp-2 text-xs text-ink-soft">
                        {item.body}
                      </span>
                      <span className="mt-1 block text-[11px] tabular-nums text-ink-faint">
                        {fmtDate(item.created_at)}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="border-t border-line px-3 py-2 text-right">
            <Link
              href="/notifications"
              onClick={() => setOpen(false)}
              className="link text-xs font-medium text-ink-soft"
            >
              View all →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationsBell;
