"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { getMe, getToken, setToken, type AuthUser } from "./api";

/**
 * Global session state for the whole app.
 *
 * The nav lives in the root layout, which Next.js does NOT remount on
 * client-side navigation — any component reading the session locally would
 * show stale "Sign in" after logging in (the exact bug this context fixes).
 * One provider owns the session; login/register pages call `signIn`, the
 * account menu and guest nudges consume it.
 *
 * Signing in or out also clears the React Query cache so responses fetched
 * for the anonymous demo session never bleed into a real account.
 */
export type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  /** Adopt a fresh session (after login/register) and drop guest caches. */
  signIn: (token: string, user: AuthUser) => void;
  /** Drop the session and any cached account data. */
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  // Initial session read happens in an effect so server and client render the
  // same markup on first paint (localStorage is client-only).
  useEffect(() => {
    let cancelled = false;
    if (getToken() === null) {
      setStatus("anonymous");
      return;
    }
    getMe()
      .then((response) => {
        if (cancelled) return;
        setUser(response.user);
        setStatus("authenticated");
      })
      .catch(() => {
        // apiFetch already dropped an invalid token on 401.
        if (cancelled) return;
        setUser(null);
        setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(
    (token: string, next: AuthUser) => {
      setToken(token);
      setUser(next);
      setStatus("authenticated");
      // Guest/demo responses must never appear under an account session.
      queryClient.clear();
    },
    [queryClient],
  );

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo(
    () => ({ user, status, signIn, signOut }),
    [user, status, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
