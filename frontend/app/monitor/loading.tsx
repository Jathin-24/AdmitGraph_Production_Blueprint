import { PageSkeleton } from "../components/ui";

/** FRONTEND_SPEC §Performance — route-level loading for Monitor. */
export default function Loading() {
  return <PageSkeleton title="Monitor" sections={3} />;
}
