/** Shared constants, helpers and types for the dashboard sections (P2-22).
 *
 * Everything here is a pure move out of page.tsx: each section imports the
 * same name the page used to close over, so rendering is unchanged. */
import type { UseQueryResult } from "@tanstack/react-query";
import { getStrategies, getStrategy, listPrograms } from "../lib/api";


export const CATEGORY_ORDER = ["REACH", "TARGET", "LOWER_RISK"];
/** FRONTEND_SPEC §Strategy — the spec'd tier headings (never the raw enum). */
export const CATEGORY_HEADING: Record<string, string> = {
  REACH: "Reach",
  TARGET: "Target",
  LOWER_RISK: "Lower-risk fit",
};
export const CATEGORY_COPY: Record<string, string> = {
  REACH: "Ambitious, worth trying",
  TARGET: "Strong match for your profile",
  LOWER_RISK: "Solid backups",
};

/** Urgency copy shared by the urgent band and the "Do this next" card. */
export function dueCopy(days: number): string {
  return days === 0
    ? "due today"
    : days < 0
      ? `${Math.abs(days)} days overdue`
      : `in ${days} day${days === 1 ? "" : "s"}`;
}

/** What the drawer shows: one program's evidence, the evidence backing a
 *  roadmap task ("Why?"), or a status filter such as conflicts. */
export type EvidenceView =
  | { programId: string }
  | { evidenceIds: string[] }
  | { status: string };


/** Query result shapes handed from page.tsx into its sections. */
export type StrategiesQuery = UseQueryResult<Awaited<ReturnType<typeof getStrategies>>, Error>;
export type StrategyQuery = UseQueryResult<Awaited<ReturnType<typeof getStrategy>>, Error>;
export type ProgramsQuery = UseQueryResult<Awaited<ReturnType<typeof listPrograms>>, Error>;
