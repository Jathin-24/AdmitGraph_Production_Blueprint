import { PageSkeleton } from "./components/ui";

/** Root route-level loading state (P2-19) — shown while a page that has no
 *  segment-level loading.tsx streams in. */
export default function Loading() {
  return <PageSkeleton title="AdmitGraph" sections={3} />;
}
