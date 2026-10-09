"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { ErrorNote } from "../components/ui";
import { ApiError } from "../lib/api";
import { isRouteUnavailable, resetPassword } from "../lib/api-extra";

interface ResetForm {
  password: string;
  confirm: string;
}

/** Same rules and copy as register (backend auth.py: password ≥ 8 chars). */
const PASSWORD_MIN = 8;

/**
 * P2-14 — set a new password from an emailed ?token= link. The token is read
 * after mount (same pattern as /login's ?next=) so server and client render
 * identical markup on first paint.
 */
export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenChecked, setTokenChecked] = useState(false);
  const [done, setDone] = useState(false);
  const [tokenInvalid, setTokenInvalid] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<ResetForm>({
    defaultValues: { password: "", confirm: "" },
    mode: "onTouched",
  });

  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("token");
    setToken(raw && raw.trim() ? raw.trim() : null);
    setTokenChecked(true);
  }, []);

  async function onSubmit(values: ResetForm): Promise<void> {
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await resetPassword(token, values.password);
      setDone(true);
    } catch (e) {
      if (isRouteUnavailable(e)) {
        setError(
          "Password reset isn’t available on this server yet — your password was NOT changed. Try again once the service is live."
        );
      } else if (e instanceof ApiError) {
        const message = e.message.toLowerCase();
        const isPasswordProblem =
          (e.code === "VALIDATION_ERROR" || e.status === 400 || e.status === 422) &&
          message.includes("password");
        if (isPasswordProblem) {
          // The backend rejected the password itself — show its exact copy.
          setError(e.message);
        } else {
          // Expired / unknown / already-used token: clear explanation + way out.
          setTokenInvalid(true);
        }
      } else {
        setError(
          "Cannot reach the server — check that the backend is running, then try again."
        );
      }
      setSubmitting(false);
    }
  }

  /* ------------------------------------------------------------- states */

  if (done) {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="status">
          <span className="chip chip-good">Done</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            Password updated
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Your new password is live — sign in to pick up your plan where you left it.
          </p>
          <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
            <Link href="/login" className="btn-primary">
              Sign in
            </Link>
          </div>
        </div>
        <Link href="/" className="btn-ghost self-start">
          ← Back to home
        </Link>
      </main>
    );
  }

  if (tokenChecked && !token) {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="alert">
          <span className="chip chip-bad">Link incomplete</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            This reset link is missing its token
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Open the link from your email again, or request a fresh one — it only takes a
            moment.
          </p>
          <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
            <Link href="/forgot-password" className="btn-primary">
              Request a new link
            </Link>
            <Link href="/login" className="btn-secondary">
              Back to sign in
            </Link>
          </div>
        </div>
      </main>
    );
  }

  if (tokenInvalid) {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="alert">
          <span className="chip chip-bad">Link expired</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            This reset link is no longer valid
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Reset links expire after a while and can only be used once. Request a new one and
            we&apos;ll email you a fresh link.
          </p>
          <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
            <Link href="/forgot-password" className="btn-primary">
              Request a new link
            </Link>
            <Link href="/login" className="btn-secondary">
              Back to sign in
            </Link>
          </div>
        </div>
      </main>
    );
  }

  /* --------------------------------------------------------------- form */

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
      <div className="border-b border-line pb-4">
        <p className="eyebrow mb-2">Account</p>
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          Set a new password
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          This replaces the password you forgot — your plan and data stay exactly as they are.
        </p>
      </div>

      <div className="card p-6">
        <form className="flex flex-col gap-5" noValidate onSubmit={handleSubmit(onSubmit)}>
          <label className="flex flex-col gap-1.5">
            <span className="label">New password</span>
            <span className="flex gap-2">
              <input
                className="field min-w-0 flex-1"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                placeholder="At least 8 characters"
                aria-invalid={errors.password ? true : undefined}
                {...register("password", {
                  required: "Password is required",
                  minLength: {
                    value: PASSWORD_MIN,
                    message: `Password must be at least ${PASSWORD_MIN} characters long`,
                  },
                })}
              />
              <button
                type="button"
                className="btn-ghost btn-sm shrink-0"
                aria-label={showPassword ? "Hide password" : "Show password"}
                onClick={() => setShowPassword((wasShown) => !wasShown)}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </span>
            <span className="hint">
              At least 8 characters. Longer passphrases work better than short complicated
              ones.
            </span>
            {errors.password && (
              <span className="text-xs text-danger" role="alert">
                {errors.password.message}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="label">Confirm new password</span>
            <input
              className="field"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              placeholder="Type it again"
              aria-invalid={errors.confirm ? true : undefined}
              {...register("confirm", {
                required: "Please confirm your password",
                validate: (value) =>
                  value === getValues().password || "Passwords don’t match",
              })}
            />
            {errors.confirm && (
              <span className="text-xs text-danger" role="alert">
                {errors.confirm.message}
              </span>
            )}
          </label>

          {error && <ErrorNote message={error} />}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-sm text-ink-faint">
              Link expired?{" "}
              <Link href="/forgot-password" className="link">
                Request a new one
              </Link>
            </p>
            <button className="btn-primary" type="submit" disabled={submitting}>
              {submitting ? "Updating…" : "Update password"}
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
