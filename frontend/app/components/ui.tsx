/**
 * Shared UI primitives + formatting helpers.
 *
 * All date formatting is string-based (slices of the ISO value) so server and
 * client always render identical markup — no hydration mismatches.
 */

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  return iso.length >= 10 ? iso.slice(0, 10) : iso;
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  if (iso.length < 16) return fmtDate(iso);
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/* ---------------------------------------------------------------- status */

export interface StatusSpec {
  label: string;
  cls: string;
}

export const STATUS_COPY: Record<string, StatusSpec> = {
  SATISFIED: { label: "Looks good", cls: "chip-good" },
  PARTIAL: { label: "Needs verification", cls: "chip-warn" },
  NOT_SATISFIED: { label: "Likely blocker", cls: "chip-bad" },
  NOT_APPLICABLE: { label: "Not required", cls: "chip-neutral" },
  UNKNOWN: { label: "Not enough evidence yet", cls: "chip-neutral" },
  CONFLICTING: { label: "Sources disagree", cls: "chip-bad" },
  NEEDS_VERIFICATION: { label: "Needs verification", cls: "chip-warn" },
  STALE: { label: "Past freshness deadline", cls: "chip-warn" },
  CURRENT: { label: "Current", cls: "chip-good" },
  UNAVAILABLE: { label: "Source unavailable", cls: "chip-neutral" },
};

export function statusSpec(status: string): StatusSpec {
  return STATUS_COPY[status] ?? STATUS_COPY.UNKNOWN;
}

export function StatusPill({ status }: { status: string }) {
  const s = statusSpec(status);
  return <span className={`chip ${s.cls}`}>{s.label}</span>;
}

const CONFIDENCE_CLS: Record<string, string> = {
  HIGH: "chip-good",
  MEDIUM: "chip-warn",
  LOW: "chip-neutral",
};

export function ConfidencePill({ confidence }: { confidence: string }) {
  return (
    <span className={CONFIDENCE_CLS[confidence] ?? "chip-neutral"}>{confidence}</span>
  );
}

/** Evidence-level status copy (FRONTEND_SPEC §Evidence drawer: source conflict
 *  status must be obvious). Requirement-level copy stays in STATUS_COPY. */
export const EVIDENCE_STATUS_COPY: Record<string, StatusSpec> = {
  CURRENT: { label: "Current", cls: "chip-good" },
  STALE: { label: "Stale — may have changed", cls: "chip-warn" },
  CONFLICTING: { label: "Conflicting information", cls: "chip-bad" },
  UNAVAILABLE: { label: "Source unavailable", cls: "chip-neutral" },
  NEEDS_VERIFICATION: { label: "Needs verification", cls: "chip-warn" },
};

export function EvidenceStatusChip({ status }: { status: string }) {
  const s = EVIDENCE_STATUS_COPY[status] ?? statusSpec(status);
  return <span className={`chip ${s.cls}`}>{s.label}</span>;
}

/* ------------------------------------------------------------ page parts */

export function PageHeader({
  eyebrow,
  title,
  lede,
  actions,
}: {
  eyebrow: string;
  title: string;
  lede?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="border-b border-line pb-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-2xl">
          <p className="eyebrow mb-2">{eyebrow}</p>
          <h1 className="display text-[2rem] font-medium leading-[1.1] text-ink">{title}</h1>
          {lede && <p className="lede mt-2">{lede}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Section({
  index,
  title,
  children,
  aside,
}: {
  index?: string;
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section className="card p-5" aria-label={title}>
      <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-line pb-3">
        <h2 className="display text-lg font-medium text-ink">
          {index && (
            <span className="mr-2 text-[13px] font-normal text-ink-faint">{index}</span>
          )}
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line-dark bg-paper/50 px-6 py-8 text-center">
      <p className="display text-base font-medium text-ink">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-faint">{body}</p>
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-danger/30 bg-danger-tint px-4 py-3 text-sm text-danger"
    >
      {message}
    </div>
  );
}

export function LoadingNote({ what }: { what: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-ink-faint" role="status">
      <span
        aria-hidden
        className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-dark border-t-forest"
      />
      {what}
    </p>
  );
}
