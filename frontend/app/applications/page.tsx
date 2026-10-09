"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader, fmtDate } from "../components/ui";
import {
  STATUS_LABELS,
  STATUS_ORDER,
  createApplication,
  deleteApplication,
  groupApplications,
  listApplications,
  patchApplication,
  statusCounts,
  statusLabel,
  type ApplicationStatus,
} from "../lib/applications-api";

/** Chip visual per status — same palette the rest of the app uses, keyed by
 *  the backend's values (unknown values fall back to neutral). */
const CHIP_BY_STATUS: Record<string, string> = {
  draft: "chip-neutral",
  submitted: "chip-neutral",
  interview: "chip-warn",
  waitlist: "chip-warn",
  offer: "chip-good",
  rejected: "chip-bad",
  withdrawn: "chip-neutral",
};

/** Status select options for one row: the pipeline order, prefixed with the
 *  row's own value when this build doesn't know it (so the select never
 *  silently shows a different status than the one the server holds). */
function rowStatusOptions(current: string): string[] {
  return (STATUS_ORDER as readonly string[]).includes(current)
    ? [...STATUS_ORDER]
    : [current, ...STATUS_ORDER];
}

export default function ApplicationsPage() {
  const queryClient = useQueryClient();

  // Add form — controlled inputs so a failed submit never loses what the
  // student typed (the reset happens in onSuccess only).
  const [university, setUniversity] = useState("");
  const [programName, setProgramName] = useState("");
  const [status, setStatus] = useState<ApplicationStatus>("draft");
  const [url, setUrl] = useState("");
  const [notes, setNotes] = useState("");

  // Row actions: a one-shot confirm id for delete, plus a shared error slot
  // for status changes / deletes (the list itself stays rendered).
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const applications = useQuery({
    queryKey: ["applications"],
    queryFn: () => listApplications(),
  });
  const items = applications.data?.items ?? [];
  const groups = groupApplications(items);
  const counts = statusCounts(items);

  const create = useMutation({
    mutationFn: () =>
      createApplication({
        university: university.trim(),
        status,
        ...(programName.trim() ? { program_name: programName.trim() } : {}),
        ...(url.trim() ? { url: url.trim() } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      }),
    onSuccess: () => {
      setUniversity("");
      setProgramName("");
      setUrl("");
      setNotes("");
      setStatus("draft");
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  const patch = useMutation({
    mutationFn: (input: { id: string; status: ApplicationStatus }) =>
      patchApplication(input.id, { status: input.status }),
    onMutate: () => setActionError(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["applications"] }),
    onError: (error: Error) => setActionError(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteApplication(id),
    onSuccess: () => {
      setConfirmingId(null);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (error: Error) => {
      // Leave the row on screen: the delete did not happen, and hiding it
      // would read as success.
      setConfirmingId(null);
      setActionError(error.message);
    },
  });

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Applications"
        title="Your application tracker."
        lede="One row per application you send: university, program, and where it stands — draft, submitted, interview, waitlist, offer, rejected or withdrawn. Everything here is exactly what you entered; nothing is inferred for you, and only your account can see it."
      />

      {/* Add */}
      <section className="card p-5" aria-label="Add an application">
        <h2 className="display mb-4 border-b border-line pb-3 text-lg font-medium">
          Add an application
        </h2>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (university.trim()) create.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-48 flex-1 flex-col gap-1">
              <span className="label">University *</span>
              <input
                className="field"
                value={university}
                onChange={(e) => setUniversity(e.target.value)}
                placeholder="TU Munich"
                required
              />
            </label>
            <label className="flex min-w-48 flex-1 flex-col gap-1">
              <span className="label">Program (optional)</span>
              <input
                className="field"
                value={programName}
                onChange={(e) => setProgramName(e.target.value)}
                placeholder="M.Sc. Robotics"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="label">Status</span>
              <select
                className="field w-auto"
                value={status}
                onChange={(e) => setStatus(e.target.value as ApplicationStatus)}
              >
                {STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-48 flex-1 flex-col gap-1">
              <span className="label">Application URL (optional)</span>
              <input
                className="field"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…"
              />
            </label>
            <label className="flex min-w-48 flex-1 flex-col gap-1">
              <span className="label">Notes (optional)</span>
              <textarea
                className="field"
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Documents sent, contact person, reference number…"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              className="btn-primary"
              disabled={!university.trim() || create.isPending}
            >
              {create.isPending ? "Adding…" : "Add application"}
            </button>
            {create.isSuccess && (
              <p className="text-sm text-forest" role="status">
                Application added.
              </p>
            )}
          </div>
          {create.isError && <ErrorNote message={(create.error as Error).message} />}
        </form>
      </section>

      {/* Grouped list */}
      <section aria-label="Applications by status">
        <h2 className="display mb-3 text-lg font-medium">Your applications</h2>

        {applications.isLoading && <LoadingNote what="Loading your applications…" />}
        {applications.isError && (
          <div className="space-y-3">
            <ErrorNote
              message={`Could not load your applications: ${(applications.error as Error).message}. The backend may be restarting — try again in a moment.`}
            />
            <button
              type="button"
              onClick={() => void applications.refetch()}
              className="btn-secondary btn-sm"
            >
              Try again
            </button>
          </div>
        )}
        {applications.isSuccess && items.length === 0 && (
          <EmptyState
            title="No applications yet"
            body="Add your first application above — university, program and status. The tracker only ever shows rows you entered yourself."
          />
        )}

        {actionError && <ErrorNote message={actionError} />}

        {items.length > 0 && (
          /* Per-status counts across the whole tracker, pipeline order —
             zero counts included so the overview never hides a stage. */
          <ul className="mb-4 flex flex-wrap gap-2" aria-label="Counts per status">
            {STATUS_ORDER.map((s) => (
              <li key={s} className={`chip ${CHIP_BY_STATUS[s]}`}>
                {STATUS_LABELS[s]} <span className="tabular-nums">{counts[s] ?? 0}</span>
              </li>
            ))}
          </ul>
        )}

        {groups.map((group) => (
          <div key={group.status} className="mb-6">
            <div className="mb-2 flex items-baseline gap-2">
              <h3 className="display text-base font-medium text-ink">{group.label}</h3>
              <span className={`chip ${CHIP_BY_STATUS[group.status] ?? "chip-neutral"}`}>
                {group.items.length}
              </span>
            </div>
            <ul className="flex flex-col gap-3">
              {group.items.map((row) => (
                <li key={row.id} className="card p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium text-ink">{row.university}</p>
                      {row.program_name && (
                        <p className="text-sm text-ink-soft">{row.program_name}</p>
                      )}
                      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
                        {row.submitted_at && <span>Submitted {fmtDate(row.submitted_at)}</span>}
                        {row.decision_at && <span>Decision {fmtDate(row.decision_at)}</span>}
                        {row.url && (
                          <a
                            href={row.url}
                            target="_blank"
                            rel="noreferrer"
                            className="link"
                          >
                            Open application →
                          </a>
                        )}
                      </p>
                      {row.notes && (
                        <p className="mt-1.5 text-sm text-ink-soft">{row.notes}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <label className="flex flex-col gap-1">
                        <span className="sr-only">Status for {row.university}</span>
                        <select
                          className="field w-auto text-sm"
                          value={row.status}
                          aria-label={`Status for ${row.university}`}
                          onChange={(e) =>
                            patch.mutate({
                              id: row.id,
                              status: e.target.value as ApplicationStatus,
                            })
                          }
                        >
                          {rowStatusOptions(row.status).map((s) => (
                            <option key={s} value={s}>
                              {statusLabel(s)}
                            </option>
                          ))}
                        </select>
                      </label>
                      {confirmingId === row.id ? (
                        <span className="flex gap-2">
                          <button
                            type="button"
                            className="btn-secondary btn-sm"
                            disabled={remove.isPending}
                            onClick={() => remove.mutate(row.id)}
                          >
                            {remove.isPending ? "Deleting…" : "Yes, delete"}
                          </button>
                          <button
                            type="button"
                            className="btn-ghost btn-sm"
                            onClick={() => setConfirmingId(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          aria-label={`Delete application for ${row.university}`}
                          onClick={() => setConfirmingId(row.id)}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                  {patch.isError && patch.variables?.id === row.id && (
                    <div className="mt-2">
                      <ErrorNote message={(patch.error as Error).message} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </main>
  );
}
