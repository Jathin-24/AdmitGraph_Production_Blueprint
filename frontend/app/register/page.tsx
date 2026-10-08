"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { ErrorNote } from "../components/ui";
import { register, setToken } from "../lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await register({
        email,
        password,
        full_name: fullName.trim() || undefined,
      });
      setToken(response.token);
      router.push("/dashboard");
    } catch (e) {
      const message = e instanceof Error ? e.message : "Account creation failed";
      setError(
        message === "Failed to fetch"
          ? "Cannot reach the server — check that the backend is running, then try again."
          : message
      );
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
      <div className="border-b border-line pb-4">
        <p className="eyebrow mb-2">Account</p>
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          Create your account
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          Local email and password — nothing leaves your control.
        </p>
      </div>

      <div className="card p-6">
        <form className="flex flex-col gap-5" onSubmit={onSubmit}>
          <label className="flex flex-col gap-1.5">
            <span className="label">Full name (optional)</span>
            <input
              className="field"
              type="text"
              name="full_name"
              autoComplete="name"
              placeholder="Ada Lovelace"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="label">Email</span>
            <input
              className="field"
              type="email"
              name="email"
              autoComplete="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="label">Password</span>
            <input
              className="field"
              type="password"
              name="password"
              autoComplete="new-password"
              required
              minLength={8}
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <span className="hint">
              At least 8 characters. Longer passphrases work better than short
              complicated ones.
            </span>
          </label>

          {error && <ErrorNote message={error} />}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-sm text-ink-faint">
              Already registered?{" "}
              <Link href="/login" className="link">
                Sign in
              </Link>
            </p>
            <button className="btn-primary" type="submit" disabled={submitting}>
              {submitting ? "Creating account…" : "Create account"}
            </button>
          </div>
        </form>
      </div>

      <Link href="/" className="btn-ghost self-start">
        ← Back to home
      </Link>
    </main>
  );
}
