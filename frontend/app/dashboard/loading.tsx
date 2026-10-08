import { PageSkeleton } from "../components/ui";

/** FRONTEND_SPEC §Performance — route-level loading for My Plan. */
export default function Loading() {
  return <PageSkeleton title="My Plan" sections={4} />;
}
