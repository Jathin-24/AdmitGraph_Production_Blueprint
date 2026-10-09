"use client";

import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { ErrorNote } from "../components/ui";
import { ApiError } from "../lib/api";
import { isRouteUnavailable, requestPasswordReset } from "../lib/api-extra";

interface ForgotForm {
  email: string;
}

/** Mirrors ONLY what the backend enforces in auth.py (same copy as register): */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * P2-14 — request a password-reset link. The response is identical whether
 * or not the address has an account (no user enumeration), and so is the
 * copy we show: one success state, always.
 */
export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<ForgotForm>({
    defaultValues: { email: "" },
    mode: "onTouched",
  });

  async function onSubmit(values: ForgotForm): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      await requestPasswordReset(values.email.trim());
      setSent(true);
    } catch (e) {
      if (isRouteUnavailable(e)) {
        setError(
          "Password reset isn’t available on this server yet — nothing was sent. Try again once the service is live."
        );
      } else if (e instanceof ApiError) {
        setError(e.message);
      } else {
        setError(
          "Cannot reach the server — check that the backend is running, then try again."
        );
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
      <div className="border-b border-line pb-4">
        <p className="eyebrow mb-2">Account</p>
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          Forgot your password?
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          We&apos;ll email you a link to set a new one.
        </p>
      </div>

      <div className="card p-6">
        {sent ? (
          /* Always the same success copy — whether or not the address exists. */
          <div role="status" className="space-y-4">
            <span className="chip chip-good">Link requested</span>
            <p className="text-sm text-ink">
              If that address has an account, we&apos;ve sent a reset link.
            </p>
            <p className="text-xs text-ink-faint">
              Didn&apos;t get it? Check spam, or{" "}
              <button
                type="button"
                className="link"
                onClick={() => {
                  setSent(false);
                  setSubmitting(false);
                }}
              >
                try another address
              </button>
              .
            </p>
            <div className="flex flex-wrap gap-3 border-t border-line pt-4">
              <Link href="/login" className="btn-primary">
                Back to sign in
              </Link>
              <Link href="/reset-password" className="btn-secondary">
                I already have a reset link
              </Link>
            </div>
          </div>
        ) : (
          <form className="flex flex-col gap-5" noValidate onSubmit={handleSubmit(onSubmit)}>
            <label className="flex flex-col gap-1.5">
              <span className="label">Email</span>
              <input
                className="field"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={errors.email ? true : undefined}
                {...register("email", {
                  required: "Email is required",
                  pattern: {
                    value: EMAIL_PATTERN,
                    message: "Enter a valid email address",
                  },
                })}
              />
              {errors.email && (
                <span className="text-xs text-danger" role="alert">
                  {errors.email.message}
                </span>
              )}
            </label>

            {error && <ErrorNote message={error} />}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p className="text-sm text-ink-faint">
                Remembered it?{" "}
                <Link href="/login" className="link">
                  Back to sign in
                </Link>
              </p>
              <button className="btn-primary" type="submit" disabled={submitting}>
                {submitting ? "Sending…" : "Send reset link"}
              </button>
            </div>
          </form>
        )}
      </div>

      {/* Cross-links keep /reset-password reachable without a nav entry (W4
          owns login/page.tsx, where the "Forgot password?" link belongs). */}
      <Link href="/reset-password" className="btn-ghost self-start">
        Already have a reset token? Set a new password →
      </Link>
    </main>
  );
}
