"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import {
  EmptyState,
  ErrorNote,
  LoadingNote,
  PageHeader,
  fmtDateTime,
} from "../components/ui";
import { TONE_CHIP, notificationMeta } from "../components/notifications-bell";
import { listNotifications, markAllNotificationsRead, markNotificationRead } from "../lib/api";
import { sanitizeHref } from "../lib/api-extra";

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: listNotifications,
    refetchOnWindowFocus: false,
  });

  const markOne = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const markAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const items = notifications.data?.items ?? [];
  const unread = items.filter((i) => !i.read);
  const earlier = items.filter((i) => i.read);
  const unreadCount = notifications.data?.unread_count ?? unread.length;

  function renderGroup(list: typeof items) {
    return (
      <ul className="flex flex-col divide-y divide-line">
        {list.map((item) => {
          const meta = notificationMeta(item.type);
          // Backend-provided link: href only for same-origin paths or real
          // http(s) URLs — anything else renders the notification as text.
          const href = sanitizeHref(item.link);
          const markRead = () => {
            if (!item.read) markOne.mutate(item.id);
          };
          const body = (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`chip ${TONE_CHIP[meta.tone]}`}>{meta.label}</span>
                {!item.read && <span className="chip chip-neutral">Unread</span>}
                {item.email_status === "FAILED" && (
                  <span
                    className="text-[11px] text-danger"
                    title="We could not email you this notification"
                  >
                    Email delivery failed
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-sm font-medium text-ink">{item.title}</p>
              {item.body && <p className="mt-0.5 text-sm text-ink-soft">{item.body}</p>}
              <p className="mt-1 text-xs tabular-nums text-ink-faint">
                {fmtDateTime(item.created_at)}
              </p>
            </>
          );
          return (
            <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                {href ? (
                  href.startsWith("/") ? (
                    <Link
                      href={href}
                      className="block hover:bg-paper-dark/40"
                      onClick={markRead}
                    >
                      {body}
                    </Link>
                  ) : (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className="block hover:bg-paper-dark/40"
                      onClick={markRead}
                    >
                      {body}
                    </a>
                  )
                ) : (
                  body
                )}
              </div>
              {!item.read ? (
                <button
                  type="button"
                  onClick={() => markOne.mutate(item.id)}
                  disabled={markOne.isPending}
                  className="btn-ghost btn-sm shrink-0"
                  aria-label={`Mark “${item.title}” as read`}
                >
                  Mark as read
                </button>
              ) : (
                <span className="shrink-0 text-xs text-ink-faint" aria-label="Read">
                  Read ✓
                </span>
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Inbox"
        title="Notifications"
        lede="Deadlines, research completions and evidence conflicts — anything that changed while you were away."
        actions={
          <button
            type="button"
            onClick={() => markAll.mutate()}
            disabled={unreadCount === 0 || markAll.isPending}
            className="btn-secondary"
          >
            Mark all as read
          </button>
        }
      />

      {notifications.isLoading && <LoadingNote what="Loading notifications…" />}
      {notifications.isError && (
        <ErrorNote
          message={
            (notifications.error as Error).message.includes("(404)")
              ? "Notifications aren’t available on this server yet — nothing is lost, and deadlines or conflicts will appear here once the service is live."
              : `Could not load notifications: ${(notifications.error as Error).message}. The service may be restarting — try again in a moment.`
          }
        />
      )}

      {notifications.isSuccess && items.length === 0 && (
        <EmptyState
          title="You're all caught up."
          body="Nothing to review yet. Notifications arrive when a deadline changes, a run finishes, or sources start to disagree."
          action={
            <Link href="/research" className="btn-primary btn-sm">
              Run the full example
            </Link>
          }
        />
      )}

      {unread.length > 0 && (
        <section aria-label="Unread">
          <h2 className="display mb-2 border-b border-line pb-2 text-lg font-medium">
            Unread{" "}
            <span className="text-sm font-normal text-ink-faint">({unread.length})</span>
          </h2>
          {renderGroup(unread)}
        </section>
      )}

      {earlier.length > 0 && (
        <section aria-label="Earlier">
          <h2 className="display mb-2 border-b border-line pb-2 text-lg font-medium">
            Earlier <span className="text-sm font-normal text-ink-faint">({earlier.length})</span>
          </h2>
          {renderGroup(earlier)}
        </section>
      )}
    </main>
  );
}
