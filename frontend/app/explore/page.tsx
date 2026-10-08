"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { EmptyState, ErrorNote, LoadingNote, PageHeader } from "../components/ui";
import { listPrograms, saveProgram, unsaveProgram } from "../lib/api";

function SaveButton({ programId, saved }: { programId: string; saved: boolean }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => (saved ? unsaveProgram(programId) : saveProgram(programId)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-programs"] }),
  });
  return (
    <button
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      aria-label={saved ? "Remove from my plan" : "Save to my plan"}
      className={saved ? "btn-secondary btn-sm" : "btn-ghost btn-sm"}
    >
      {saved ? "Saved ✓" : "Save"}
    </button>
  );
}

export default function ExplorePage() {
  const [page, setPage] = useState(1);
  const [savedOnly, setSavedOnly] = useState(false);
  const queryClient = useQueryClient();

  const programs = useQuery({
    queryKey: ["programs", page, savedOnly],
    queryFn: () => listPrograms(page, 20, savedOnly),
    placeholderData: (prev) => prev,
  });
  // Saved membership for the Save/Saved column — same queryKey + shape as
  // the program page, normalized to a Set via select.
  const saved = useQuery({
    queryKey: ["saved-programs"],
    queryFn: () => listPrograms(1, 100, true),
    select: (raw: unknown): Set<string> =>
      raw instanceof Set
        ? (raw as Set<string>)
        : new Set(
            ((raw as { items?: { id: string }[] })?.items ?? []).map((sp) => sp.id)
          ),
  });

  const total = programs.data?.total ?? 0;
  const pageSize = programs.data?.page_size ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-5 py-8">
      <PageHeader
        eyebrow="Catalog"
        title="Explore programs"
        lede="Programs discovered during live research runs — every row traces back to stored evidence, never to guesses."
        actions={
          <label className="flex items-center gap-2 self-end text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={savedOnly}
              onChange={(e) => {
                setSavedOnly(e.target.checked);
                setPage(1);
              }}
              className="h-4 w-4 accent-[#1D5C46]"
            />
            Saved only
          </label>
        }
      />

      {programs.isLoading && <LoadingNote what="Loading programs…" />}
      {programs.isError && (
        <ErrorNote
          message={`Could not load programs: ${(programs.error as Error).message}. The backend may be restarting — try again in a moment.`}
        />
      )}

      {programs.isSuccess && programs.data.items.length === 0 && (
        <EmptyState
          title={savedOnly ? "Nothing saved yet" : "No programs yet"}
          body={
            savedOnly
              ? "Save programs from the full list to build your shortlist here."
              : "Programs appear after a research run discovers them — start with the full example."
          }
          action={
            <Link href="/research" className="btn-primary btn-sm">
              Run the full example
            </Link>
          }
        />
      )}

      {programs.isSuccess && programs.data.items.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table-editorial min-w-[640px]">
              <thead>
                <tr>
                  <th className="pl-5">Program</th>
                  <th>Country</th>
                  <th>Level</th>
                  <th>Field</th>
                  <th className="pr-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {programs.data.items.map((p) => (
                  <tr key={p.id}>
                    <td className="pl-5">
                      <Link
                        href={`/programs/${p.id}`}
                        className="display text-[15px] font-medium text-ink decoration-forest underline-offset-4 hover:underline"
                      >
                        {p.name}
                      </Link>
                    </td>
                    <td className="text-ink-soft">{p.country_code ?? "—"}</td>
                    <td className="text-ink-soft">{p.degree_type ?? "—"}</td>
                    <td className="text-ink-soft">{p.field_of_study ?? "—"}</td>
                    <td className="pr-5">
                      <div className="flex items-center justify-end gap-2">
                        {p.official_url && (
                          <a
                            href={p.official_url}
                            target="_blank"
                            rel="noreferrer"
                            className="link text-xs text-ink-faint"
                            aria-label={`Official site for ${p.name}`}
                          >
                            Official ↗
                          </a>
                        )}
                        <SaveButton programId={p.id} saved={saved.data?.has(p.id) ?? false} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <nav
            className="flex items-center justify-between border-t border-line px-5 py-3 text-sm text-ink-soft"
            aria-label="Pagination"
          >
            <button
              className="btn-ghost btn-sm"
              disabled={page <= 1}
              onClick={() => {
                setPage(page - 1);
                queryClient.invalidateQueries({ queryKey: ["programs"] });
              }}
            >
              ← Previous
            </button>
            <span className="text-xs uppercase tracking-wide text-ink-faint">
              Page {page} of {totalPages} · {total} programs
            </span>
            <button
              className="btn-ghost btn-sm"
              disabled={page >= totalPages}
              onClick={() => {
                setPage(page + 1);
                queryClient.invalidateQueries({ queryKey: ["programs"] });
              }}
            >
              Next →
            </button>
          </nav>
        </div>
      )}
    </main>
  );
}
