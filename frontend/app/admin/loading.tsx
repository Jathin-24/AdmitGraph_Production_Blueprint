import { PageSkeleton } from "../components/ui";

/** Route-level loading for the admin console (P2-16). */
export default function Loading() {
  return <PageSkeleton title="Admin" sections={3} />;
}
