"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "./lib/api";
import { AuthProvider } from "./lib/auth";
import { ParseError } from "./lib/schemas";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // One quick retry for 5xx/network blips. Never retry a 4xx
            // (ApiError carries the real status — no message matching) or a
            // schema ParseError (a retry cannot fix a shape mismatch), so a
            // missing/409/429 resource doesn't refetch in a loop.
            retry: (failureCount, error) => {
              if (error instanceof ParseError) return false;
              if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
                return false; // 4xx: the request itself is wrong — no retry
              }
              return failureCount < 1;
            },
            refetchOnWindowFocus: false,
            staleTime: 10_000,
          },
        },
      })
  );
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
