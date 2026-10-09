import type { Metadata } from "next";
import { API_BASE } from "../../lib/api";
import ProgramDetailPage from "./program-detail";

/**
 * P2-20 — per-route metadata for the program detail page.
 *
 * The detail view itself is a client component (react-query + hooks), so this
 * thin server wrapper stays around it and fetches just enough of the program
 * to title the tab. Any backend failure degrades to a generic title — the
 * page never depends on this fetch succeeding.
 */

interface ProgramMeta {
  name: string;
  institution: string | null;
}

async function fetchProgramMeta(id: string): Promise<ProgramMeta | null> {
  try {
    const res = await fetch(`${API_BASE}/programs/${encodeURIComponent(id)}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { name?: unknown; institution?: unknown };
    if (typeof body?.name !== "string" || body.name.trim() === "") return null;
    return {
      name: body.name,
      institution: typeof body.institution === "string" ? body.institution : null,
    };
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: { id: string };
}): Promise<Metadata> {
  const program = await fetchProgramMeta(params.id);
  if (!program) return { title: "Program" };
  const title = program.institution
    ? `${program.name} — ${program.institution}`
    : program.name;
  return {
    title,
    description: `Requirements, costs, deadlines and sourced evidence for ${title}.`,
  };
}

export default function Page() {
  return <ProgramDetailPage />;
}
