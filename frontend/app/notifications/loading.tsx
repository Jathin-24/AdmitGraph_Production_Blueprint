import { PageSkeleton } from "../components/ui";

/** FRONTEND_SPEC §Performance — route-level loading for Notifications. */
export default function Loading() {
  return <PageSkeleton title="Notifications" sections={2} />;
}
