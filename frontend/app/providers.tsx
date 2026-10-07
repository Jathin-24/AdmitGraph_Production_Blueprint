"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // One quick retry, no retry storms on 4xx — keeps the console clean
            // while the backend restarts or a resource is simply missing.
            retry: (failureCount, error) => {
              const message = error instanceof Error ? error.message : "";
              if (message.includes("(4")) return false; // 4xx: don't retry
              return failureCount < 1;
            },
            refetchOnWindowFocus: false,
            staleTime: 10_000,
          },
        },
      })
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
