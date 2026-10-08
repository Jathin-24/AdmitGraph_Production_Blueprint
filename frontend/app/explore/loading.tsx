import { PageSkeleton } from "../components/ui";

/** FRONTEND_SPEC §Performance — route-level loading for Explore. */
export default function Loading() {
  return <PageSkeleton title="Explore" sections={3} />;
}
