import Link from "next/link";

/** 404 page (P2-19) — plain recovery paths, no dead ends. */
export default function NotFound() {
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 px-5 py-16">
      <p className="eyebrow">Page not found</p>
      <h1 className="display text-2xl font-medium leading-tight text-ink">
        We couldn&apos;t find that page.
      </h1>
      <p className="text-sm text-ink-soft">
        The link may be old, or the program or plan it pointed to is gone. Everything else
        still works — start from the home page or browse the catalog.
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        <Link href="/" className="btn-primary">
          Go home
        </Link>
        <Link href="/explore" className="btn-secondary">
          Explore programs
        </Link>
      </div>
    </main>
  );
}
