"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
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
      className={`rounded-full border px-3 py-1 text-xs ${
        saved ? "border-black bg-black text-white" : "border-neutral-300 hover:border-black"
      }`}
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
  // Saved list drives the Save/Saved toggle state (membership check).
  const saved = useQuery({
    queryKey: ["saved-programs"],
    queryFn: () => listPrograms(1, 100, true),
  });
  const savedIds = new Set((saved.data?.items ?? []).map((p) => p.id));

  const total = programs.data?.total ?? 0;
  const pageSize = programs.data?.page_size ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <main className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Explore programs</h1>
          <p className="text-sm text-neutral-500">
            Programs discovered during live research runs — every row traces back to stored
            evidence, never to guesses.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={savedOnly}
            onChange={(e) => {
              setSavedOnly(e.target.checked);
              setPage(1);
            }}
          />
          Show only saved
        </label>
      </div>

      {programs.isLoading && <p className="text-neutral-500">Loading programs…</p>}

      {programs.isError && (
        <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm">
          Could not load programs: {(programs.error as Error).message}. The backend may be
          restarting — try again in a moment.
        </div>
      )}

      {programs.isSuccess && programs.data.items.length === 0 && (
        <div className="rounded border border-dashed p-8 text-center">
          <p className="mb-2 font-medium">No programs yet</p>
          <p className="mb-4 text-sm text-neutral-500">
            {savedOnly
              ? "You haven't saved any programs yet."
              : "Programs appear after a live research run discovers them."}
          </p>
          <Link href="/research" className="rounded-full bg-black px-5 py-2 text-sm text-white">
            Run live research
          </Link>
        </div>
      )}

      {programs.isSuccess && programs.data.items.length > 0 && (
        <>
          <ul className="grid gap-3 sm:grid-cols-2">
            {programs.data.items.map((p) => (
              <li
                key={p.id}
                className="flex flex-col gap-2 rounded-xl border border-neutral-200 p-4"
              >
                <Link
                  href={`/programs/${p.id}`}
                  className="font-medium leading-snug hover:underline"
                >
                  {p.name}
                </Link>
                <div className="flex flex-wrap gap-1.5 text-xs text-neutral-500">
                  {p.country_code && (
                    <span className="rounded bg-neutral-100 px-1.5 py-0.5">{p.country_code}</span>
                  )}
                  {p.degree_type && (
                    <span className="rounded bg-neutral-100 px-1.5 py-0.5">{p.degree_type}</span>
                  )}
                  {p.field_of_study && (
                    <span className="rounded bg-neutral-100 px-1.5 py-0.5">{p.field_of_study}</span>
                  )}
                </div>
                <div className="mt-auto flex items-center justify-between gap-2">
                  {p.official_url ? (
                    <a
                      href={p.official_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs underline text-neutral-500"
                    >
                      Official site ↗
                    </a>
                  ) : (
                    <span className="text-xs text-neutral-400">Official URL unknown</span>
                  )}
                  <SaveButton programId={p.id} saved={savedIds.has(p.id)} />
                </div>
              </li>
            ))}
          </ul>

          <nav className="mt-6 flex items-center justify-between text-sm" aria-label="Pagination">
            <button
              className="rounded border px-3 py-1.5 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => {
                setPage(page - 1);
                queryClient.invalidateQueries({ queryKey: ["programs"] });
              }}
            >
              ← Previous
            </button>
            <span className="text-neutral-500">
              Page {page} of {totalPages} · {total} programs
            </span>
            <button
              className="rounded border px-3 py-1.5 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => {
                setPage(page + 1);
                queryClient.invalidateQueries({ queryKey: ["programs"] });
              }}
            >
              Next →
            </button>
          </nav>
        </>
      )}
    </main>
  );
}
