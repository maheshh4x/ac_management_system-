"use client";

/**
 * TanStack Query provider (Phase 2, task 3).
 * Global error handling surfaces API errors as readable text via the
 * ApiError message; retry only for 5xx/network failures (never 4xx, never
 * client timeouts — a timed-out server request may still be running, so
 * re-issuing it would duplicate the work server-side).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "./client";

export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof ApiError) {
    // TIMEOUT (status 0) must not retry: the aborted request may still be
    // executing server-side. NETWORK_ERROR (also status 0) is a failed
    // request that never reached the server, so it stays retryable.
    if (error.code === "TIMEOUT") return false;
    if (error.status === 0) return true;
    if (error.status >= 500) return true;
    return false;
  }
  return true;
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: shouldRetry,
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: false },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
