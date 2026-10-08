"use client";

/**
 * Evidence re-check + conflict resolution actions (FRONTEND_SPEC §Error
 * states: provider unavailable / conflicting evidence).
 *
 * Both actions call the backend and only report what actually happened —
 * every failure mode names the reason and the next step.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, recheckEvidence, resolveConflict } from "../lib/api";

/** Honest copy per failure mode — never "Something went wrong" alone. */
function recheckError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409 || err.code === "PROVIDER_UNAVAILABLE") {
      return "Provider unavailable — configure SERPAPI_API_KEY on the server to re-check claims. Nothing was changed.";
    }
    if (err.status === 404) {
      return "This claim is no longer on the server — it may have been cleaned up. Refresh the page to see current evidence.";
    }
    if (err.status === 502) {
      return "The source did not answer our re-check just now — try again in a minute. The existing claim is untouched.";
    }
    if (err.status === 401) return err.message;
    return err.message;
  }
  return "The re-check could not reach the server — check your connection and try again. The existing claim is untouched.";
}

/** Re-check one claim (1 search + extraction) and refresh evidence queries. */
export function RecheckButton({
  evidenceId,
  className = "link text-xs",
  label = "Re-check this claim",
}: {
  evidenceId: string;
  className?: string;
  label?: string;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const recheck = useMutation({
    mutationFn: () => recheckEvidence(evidenceId),
    onSuccess: (out) => {
      setNote(null);
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
      void queryClient.invalidateQueries({ queryKey: ["program-evidence"] });
      void queryClient.invalidateQueries({ queryKey: ["evidence-health"] });
      const status = typeof out?.status === "string" ? out.status.toLowerCase() : "checked";
      setNote(`Re-check finished — claim status is now ${status}.`);
    },
    onError: (err) => setNote(recheckError(err)),
  });

  return (
    <span className="inline-flex flex-col gap-1">
      <button
        type="button"
        className={className}
        onClick={() => {
          setNote(null);
          recheck.mutate();
        }}
        disabled={recheck.isPending}
      >
        {recheck.isPending ? "Re-checking…" : label}
      </button>
      {note && !recheck.isPending && (
        <span
          role="status"
          className={`text-xs ${recheck.isError ? "text-danger" : "text-ink-soft"}`}
        >
          {note}
        </span>
      )}
    </span>
  );
}

/** Resolve a conflict group (empty body ⇒ authority-preferred member wins). */
export function ResolveConflictButton({
  conflictId,
  className = "link text-xs",
}: {
  conflictId: string;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const resolve = useMutation({
    mutationFn: () => resolveConflict(conflictId),
    onSuccess: () => {
      setNote("Conflict resolved — the higher-authority source was kept.");
      void queryClient.invalidateQueries({ queryKey: ["evidence-conflicts"] });
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
      void queryClient.invalidateQueries({ queryKey: ["program-evidence"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.code === "ALREADY_RESOLVED" || err.status === 409) {
          setNote("This conflict was already resolved — nothing to do.");
          return;
        }
        if (err.status === 404) {
          setNote("This conflict is no longer on the server — refresh to see the current state.");
          return;
        }
        setNote(err.message);
        return;
      }
      setNote("The conflict could not be resolved right now — try again shortly.");
    },
  });

  return (
    <span className="inline-flex flex-col gap-1">
      <button
        type="button"
        className={className}
        onClick={() => {
          setNote(null);
          resolve.mutate();
        }}
        disabled={resolve.isPending}
      >
        {resolve.isPending ? "Resolving…" : "Resolve conflict"}
      </button>
      {note && !resolve.isPending && (
        <span
          role="status"
          className={`text-xs ${resolve.isError ? "text-danger" : "text-ink-soft"}`}
        >
          {note}
        </span>
      )}
    </span>
  );
}
