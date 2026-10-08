"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { ErrorNote } from "../components/ui";
import { register } from "../lib/api";
import { useAuth } from "../lib/auth";

interface RegisterForm {
  full_name: string;
  email: string;
  password: string;
}

/** Mirrors ONLY what the backend enforces in auth.py (same copy as the API):
 *  email must look like local@domain.tld, password ≥ 8 characters.
 *  full_name is optional and unconstrained. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_MIN = 8;

function safeNextPath(): string | null {
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next");
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return null;
}

export default function RegisterPage() {
  const router = useRouter();
  const { status, signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [nextPath, setNextPath] = useState<string | null>(null);

  const {
    register: registerField,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterForm>({
    defaultValues: { full_name: "", email: "", password: "" },
    mode: "onTouched",
  });

  // Read after mount so server and client render identical first paint.
  useEffect(() => {
    setNextPath(safeNextPath());
  }, []);

  // Already signed in: sending an authenticated user to the sign-up form is
  // a dead end — leave for their destination instead.
  useEffect(() => {
    if (status === "authenticated") router.replace(nextPath ?? "/dashboard");
  }, [status, nextPath, router]);

  async function onSubmit(values: RegisterForm): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      const response = await register({
        email: values.email,
        password: values.password,
        full_name: values.full_name.trim() || undefined,
      });
      // Publish to the global auth context before navigating so the nav
      // reflects the new session immediately.
      signIn(response.token, response.user);
      // A brand-new account has an empty profile: land in the wizard unless
      // something specific is waiting (?next=…).
      router.push(nextPath ?? "/onboarding");
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

  const loginHref = nextPath ? `/login?next=${encodeURIComponent(nextPath)}` : "/login";

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
        <form className="flex flex-col gap-5" noValidate onSubmit={handleSubmit(onSubmit)}>
          <label className="flex flex-col gap-1.5">
            <span className="label">Full name (optional)</span>
            <input
              className="field"
              type="text"
              autoComplete="name"
              placeholder="Ada Lovelace"
              {...registerField("full_name")}
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="label">Email</span>
            <input
              className="field"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              aria-invalid={errors.email ? true : undefined}
              {...registerField("email", {
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
            <span className="label">Password</span>
            <span className="flex gap-2">
              <input
                className="field min-w-0 flex-1"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                placeholder="At least 8 characters"
                aria-invalid={errors.password ? true : undefined}
                {...registerField("password", {
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
              At least 8 characters. Longer passphrases work better than short
              complicated ones.
            </span>
            {errors.password && (
              <span className="text-xs text-danger" role="alert">
                {errors.password.message}
              </span>
            )}
          </label>

          {error && <ErrorNote message={error} />}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-sm text-ink-faint">
              Already registered?{" "}
              <Link href={loginHref} className="link">
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
