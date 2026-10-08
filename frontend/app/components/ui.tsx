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
  FRESH: { label: "Fresh", cls: "chip-good" },
  CURRENT: { label: "Current", cls: "chip-good" },
  STALE: { label: "Stale — may have changed", cls: "chip-warn" },
  UNKNOWN: { label: "Not enough evidence yet", cls: "chip-neutral" },
  CONFLICTING: { label: "Conflicting information", cls: "chip-bad" },
  UNAVAILABLE: { label: "Source unavailable", cls: "chip-neutral" },
  NEEDS_VERIFICATION: { label: "Needs verification", cls: "chip-warn" },
};

export function EvidenceStatusChip({ status }: { status: string }) {
  const s = EVIDENCE_STATUS_COPY[status] ?? statusSpec(status);
  return <span className={`chip ${s.cls}`}>{s.label}</span>;
}

/* --------------------------------------------------------------- severity */

/** FRONTEND_SPEC §Risk UI — the only risk wording a student should see.
 *  Keys are lower-case so "CRITICAL", "critical" and "High" all resolve. */
export const SEVERITY_COPY: Record<string, string> = {
  critical: "Do not apply yet",
  high: "Fix before applying",
  medium: "Verify before deciding",
  low: "Good to know",
};

/** Friendly label + the existing chip visual (bad/warn/neutral). */
export function severitySpec(severity: string): StatusSpec {
  const key = (severity ?? "").toLowerCase();
  const label = SEVERITY_COPY[key] ?? severity;
  const cls =
    key === "critical" || key === "high"
      ? "chip-bad"
      : key === "medium"
        ? "chip-warn"
        : "chip-neutral";
  return { label, cls };
}

/** Risk severity chip: friendly copy is what you read, the raw backend value
 *  stays discoverable (tooltip + aria-label, optionally as visible subtext). */
export function SeverityChip({
  severity,
  showRaw = false,
  className = "",
}: {
  severity: string;
  showRaw?: boolean;
  className?: string;
}) {
  const s = severitySpec(severity);
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span
        className={`chip ${s.cls} ${className}`}
        title={`${severity} — ${s.label}`}
        aria-label={`${severity}: ${s.label}`}
      >
        {s.label}
      </span>
      {showRaw && (
        <span
          aria-hidden
          className="text-[10px] font-medium uppercase tracking-wide text-ink-faint"
        >
          {severity}
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------ skeletons */

/** One shimmering block for route-level loading states (FRONTEND_SPEC
 *  §Performance: skeletons + route-level loading). */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span aria-hidden className={`block animate-pulse rounded bg-line/70 ${className}`} />;
}

/** Stand-in page shell shown while a route's data resolves. */
export function PageSkeleton({
  sections = 3,
  title = "Loading",
}: {
  sections?: number;
  title?: string;
}) {
  return (
    <main
      className="mx-auto max-w-5xl space-y-6 px-5 py-8"
      role="status"
      aria-label={`${title} — loading`}
    >
      <header className="border-b border-line pb-6">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-8 w-72 max-w-full" />
        <Skeleton className="mt-3 h-4 w-[32rem] max-w-full" />
      </header>
      {Array.from({ length: sections }).map((_, index) => (
        <section key={index} className="card space-y-3 p-5">
          <Skeleton className="h-4 w-44" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-11/12" />
          <Skeleton className="h-3 w-2/3" />
        </section>
      ))}
      <span className="sr-only">Loading…</span>
    </main>
  );
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

/**
 * Native `<details>` disclosure — no client JS, keyboard accessible by
 * default, and it renders identically on the server. Used to push reference
 * material ("Evidence health", "What could break this plan?") below the
 * decision a student actually came here to make, instead of stacking eight
 * equal-weight cards on one screen.
 */
export function Disclosure({
  summary,
  hint,
  defaultOpen = false,
  children,
}: {
  summary: string;
  hint?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="card p-5" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-baseline justify-between gap-4 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-forest">
        <span className="display text-lg font-medium text-ink">
          {summary}
          {hint && <span className="ml-2 text-sm font-normal text-ink-faint">{hint}</span>}
        </span>
        <span
          aria-hidden
          className="text-ink-faint transition-transform [details[open]_&]:rotate-180"
        >
          ▾
        </span>
      </summary>
      <div className="mt-4">{children}</div>
    </details>
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
