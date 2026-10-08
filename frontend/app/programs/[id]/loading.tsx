import { PageSkeleton } from "../../components/ui";

/** FRONTEND_SPEC §Performance — route-level loading for program detail. */
export default function Loading() {
  return <PageSkeleton title="Program" sections={5} />;
}
