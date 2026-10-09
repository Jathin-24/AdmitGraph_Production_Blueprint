"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { ErrorNote } from "../components/ui";
import { getOnboardingProgress, login } from "../lib/api";
import { useAuth } from "../lib/auth";

interface LoginForm {
  email: string;
  password: string;
}

/** Mirrors ONLY what the backend enforces in auth.py:
 *  an email must look like local@domain.tld (no spaces) and a password must
 *  be at least MIN_PASSWORD_LENGTH (8) characters — same copy as the API. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_MIN = 8;

function safeNextPath(): string | null {
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next");
  // Same-origin paths only — never redirect off-site after signing in.
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return null;
}

/** Sign-in lands where the student still has work to do: unfinished profile
 *  -> the wizard; a complete profile -> the plan dashboard. */
async function landingPath(): Promise<string> {
  try {
    const progress = await getOnboardingProgress();
    return progress.missing_required_keys.length > 0 ? "/onboarding" : "/dashboard";
  } catch {
    return "/dashboard";
  }
}

export default function LoginPage() {
  const router = useRouter();
  const { status, signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [nextPath, setNextPath] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginForm>({
    defaultValues: { email: "", password: "" },
    mode: "onTouched",
  });

  // Deep-link state (?expired=1&next=…) is read after mount so the server and
  // client render identical markup on first paint.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired")) setExpired(true);
    setNextPath(safeNextPath());
  }, []);

  // Already signed in: no reason to sit on the sign-in form.
  useEffect(() => {
    if (status === "authenticated") router.replace(nextPath ?? "/dashboard");
  }, [status, nextPath, router]);

  async function onSubmit(values: LoginForm): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      const response = await login(values);
      // Publish the session to the global auth context BEFORE navigating so
      // the nav account menu updates immediately (no stale "Sign in").
      signIn(response.token, response.user);
      router.push(nextPath ?? (await landingPath()));
    } catch (e) {
      const message = e instanceof Error ? e.message : "Sign-in failed";
      setError(
        message === "Failed to fetch"
          ? "Cannot reach the server — check that the backend is running, then try again."
          : message
      );
      setSubmitting(false);
    }
  }

  const registerHref = nextPath ? `/register?next=${encodeURIComponent(nextPath)}` : "/register";

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
      <div className="border-b border-line pb-4">
        <p className="eyebrow mb-2">Account</p>
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          Welcome back
        </h1>
        <p className="mt-1 text-sm text-ink-faint">
          Sign in to pick up your plan where you left it.
        </p>
      </div>

      <div className="card p-6">
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

          <label className="flex flex-col gap-1.5">
            <span className="flex items-baseline justify-between gap-2">
              <span className="label">Password</span>
              {/* Same recovery path as the copy on /forgot-password. */}
              <Link href="/forgot-password" className="link text-xs">
                Forgot password?
              </Link>
            </span>
            <span className="flex gap-2">
              <input
                className="field min-w-0 flex-1"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                placeholder="Your password"
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
            {errors.password && (
              <span className="text-xs text-danger" role="alert">
                {errors.password.message}
              </span>
            )}
          </label>

          {expired && (
            <ErrorNote message="Session expired, please sign in again." />
          )}
          {error && <ErrorNote message={error} />}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-sm text-ink-faint">
              New here?{" "}
              <Link href={registerHref} className="link">
                Create an account
              </Link>
            </p>
            <button className="btn-primary" type="submit" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
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
