"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ErrorNote, LoadingNote } from "../components/ui";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { isRouteUnavailable, requestEmailVerification, verifyEmail } from "../lib/api-extra";

type Phase = "verifying" | "success" | "invalid" | "error";

/**
 * P2-14 — consume an emailed ?token= link and stamp the address verified.
 * The token is read after mount (same pattern as /reset-password) so server
 * and client render identical markup on first paint, and verification runs
 * automatically once the token is found.
 */
export default function VerifyEmailPage() {
  const { user, status } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [tokenChecked, setTokenChecked] = useState(false);
  const [phase, setPhase] = useState<Phase>("verifying");
  const [error, setError] = useState<string | null>(null);
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");

  const runVerify = useCallback(async (rawToken: string) => {
    setPhase("verifying");
    setError(null);
    try {
      await verifyEmail(rawToken);
      setPhase("success");
    } catch (e) {
      if (isRouteUnavailable(e)) {
        setError(
          "Email verification isn’t available on this server yet — your address was NOT verified. Try again once the service is live."
        );
        setPhase("error");
      } else if (e instanceof ApiError) {
        // Expired / unknown / already-used token: clear explanation + way out.
        setPhase("invalid");
      } else {
        setError(
          "Cannot reach the server — check that the backend is running, then try again."
        );
        setPhase("error");
      }
    }
  }, []);

  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("token");
    const found = raw && raw.trim() ? raw.trim() : null;
    setToken(found);
    setTokenChecked(true);
    if (found) void runVerify(found);
  }, [runVerify]);

  async function onResend(): Promise<void> {
    if (!user?.email) return;
    setResendState("sending");
    setError(null);
    try {
      await requestEmailVerification(user.email);
      setResendState("sent");
    } catch (e) {
      setResendState("idle");
      if (isRouteUnavailable(e)) {
        setError(
          "Email verification isn’t available on this server yet — no email was sent. Try again once the service is live."
        );
      } else if (e instanceof ApiError) {
        setError(e.message);
      } else {
        setError(
          "Cannot reach the server — check that the backend is running, then try again."
        );
      }
    }
  }

  /** "Resend" offer for signed-in accounts — the backend needs an address and
   *  we already know the one on the session (never ask, never guess). */
  const resend =
    status === "authenticated" && user?.email ? (
      resendState === "sent" ? (
        <p className="text-xs text-ink-faint" role="status">
          If that address has an account, we&apos;ve sent a fresh verification link — open it
          from your email to finish verifying.
        </p>
      ) : (
        <p className="text-sm text-ink-faint">
          Need another link?{" "}
          <button
            type="button"
            className="link"
            disabled={resendState === "sending"}
            onClick={() => void onResend()}
          >
            {resendState === "sending" ? "Sending…" : "Resend verification email"}
          </button>
        </p>
      )
    ) : (
      <p className="text-sm text-ink-faint">
        Not signed in?{" "}
        <Link href="/login" className="link">
          Sign in
        </Link>{" "}
        and request a new verification link from your account.
      </p>
    );

  /* ------------------------------------------------------------- states */

  if (tokenChecked && !token) {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="alert">
          <span className="chip chip-bad">Link incomplete</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            This verification link is missing its token
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Open the link from your email again — it should include everything after
            &quot;?token=&quot;. If it still fails, request a fresh link.
          </p>
          <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4">
            {resend}
            <div className="flex flex-wrap gap-3">
              <Link href="/login" className="btn-primary">
                Go to sign in
              </Link>
            </div>
          </div>
        </div>
      </main>
    );
  }

  if (phase === "success") {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="status">
          <span className="chip chip-good">Verified</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            Email verified — you can sign in
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Your address is confirmed. Sign in to pick up your plan where you left it.
          </p>
          <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
            <Link href="/login" className="btn-primary">
              Sign in
            </Link>
            <Link href="/" className="btn-secondary">
              Back to home
            </Link>
          </div>
        </div>
      </main>
    );
  }

  if (phase === "invalid") {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="card p-6" role="alert">
          <span className="chip chip-bad">Link expired</span>
          <h1 className="display mt-3 text-[1.75rem] font-medium leading-tight text-ink">
            This verification link is invalid or has expired
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Verification links expire after a while and can only be used once — if you
            already verified, you&apos;re all set. Otherwise request a fresh link and
            we&apos;ll email you a new one.
          </p>
          <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4">
            {resend}
            <div className="flex flex-wrap gap-3">
              <Link href="/login" className="btn-primary">
                Go to sign in
              </Link>
            </div>
          </div>
        </div>
      </main>
    );
  }

  if (phase === "error") {
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
        <div className="border-b border-line pb-4">
          <p className="eyebrow mb-2">Account</p>
          <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
            Verify your email
          </h1>
        </div>
        <div className="card p-6">
          {error && <ErrorNote message={error} />}
          <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
            {token && (
              <button
                type="button"
                className="btn-primary"
                onClick={() => void runVerify(token)}
              >
                Try again
              </button>
            )}
            <Link href="/login" className="btn-secondary">
              Go to sign in
            </Link>
          </div>
        </div>
      </main>
    );
  }

  /* ----------------------------------------------------------- verifying */

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-5 py-16">
      <div className="border-b border-line pb-4">
        <p className="eyebrow mb-2">Account</p>
        <h1 className="display text-[1.75rem] font-medium leading-tight text-ink">
          Verifying your email
        </h1>
        <p className="mt-1 text-sm text-ink-faint">This only takes a moment.</p>
      </div>
      <div className="card p-6">
        <LoadingNote what="Checking your verification link…" />
      </div>
    </main>
  );
}
