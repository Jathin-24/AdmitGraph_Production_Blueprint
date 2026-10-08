"use client";

/**
 * Risk status actions (FRONTEND_SPEC §Risk UI).
 *
 * Acknowledge / Resolve / Dismiss all PATCH the risk's status on the backend —
 * the UI never pretends a risk changed without the server confirming it.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, updateRisk } from "../lib/api";

type RiskStatus = "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED";

const ACTIONS: { status: RiskStatus; label: string; pending: string }[] = [
  { status: "ACKNOWLEDGED", label: "Acknowledge", pending: "Acknowledging…" },
  { status: "RESOLVED", label: "Resolve", pending: "Resolving…" },
  { status: "DISMISSED", label: "Dismiss", pending: "Dismissing…" },
];

/** Failure copy that names what happened (never "Something went wrong"). */
function patchError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return "This risk is no longer in your profile — it may have been regenerated. Refresh to see the current list.";
    }
    if (err.status === 422) {
      return "That status change isn’t allowed for this risk — refresh and pick another action.";
    }
    if (err.status === 401) return err.message;
    return err.message;
  }
  return "The update didn’t reach the server — check your connection and try again. The risk is unchanged.";
}

export function RiskActions({
  strategyId,
  riskId,
  status,
}: {
  strategyId: string;
  riskId: string;
  status: string;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: (next: RiskStatus) => updateRisk(strategyId, riskId, next),
    onSuccess: (_out, next) => {
      const copy =
        next === "ACKNOWLEDGED"
          ? "Risk acknowledged — it stays visible until it’s resolved."
          : next === "RESOLVED"
            ? "Risk marked resolved."
            : "Risk dismissed — it will only return if new evidence raises it again.";
      setNote(copy);
      void queryClient.invalidateQueries({ queryKey: ["strategy", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["risks", strategyId] });
    },
    onError: (err) => setNote(patchError(err)),
  });

  const settled = status === "RESOLVED" || status === "DISMISSED";

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {ACTIONS.filter((a) => !(status === a.status)).map((a) => (
        <button
          key={a.status}
          type="button"
          className="btn-ghost btn-sm"
          disabled={update.isPending || settled}
          onClick={() => {
            setNote(null);
            update.mutate(a.status);
          }}
        >
          {update.isPending && update.variables === a.status ? a.pending : a.label}
        </button>
      ))}
      {settled && (
        <span className="text-xs text-ink-faint">Status: {status.toLowerCase()}</span>
      )}
      {note && !update.isPending && (
        <span role="status" className={`text-xs ${update.isError ? "text-danger" : "text-ink-soft"}`}>
          {note}
        </span>
      )}
    </span>
  );
}
