import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <h1 className="text-4xl font-semibold tracking-tight">Study abroad with a plan, not a pile of tabs.</h1>
      <p className="max-w-xl text-lg text-neutral-600">
        AdmitGraph checks live requirements, costs, deadlines and risks against your profile — then tells you what to do next.
      </p>
      <div className="flex gap-4">
        <Link href="/research" className="rounded-full bg-black px-6 py-3 text-white">
          Build my strategy
        </Link>
        <Link href="/onboarding" className="rounded-full border px-6 py-3">
          First time studying abroad? Start here
        </Link>
      </div>
    </main>
  );
}
