/**
 * Retry policy contract (Stage E2 Batch 1): a client TIMEOUT must never be
 * retried automatically — the aborted request may still be executing
 * server-side, so a retry would duplicate the work. NETWORK_ERROR (request
 * never reached the server) and transient HTTP 5xx stay retryable; ordinary
 * 4xx are never retried; at most 2 retries per query.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { shouldRetry } from "./query-provider";

function err(code: string, status: number): ApiError {
  return new ApiError(code, "test error", status, null);
}

describe("shouldRetry", () => {
  it("never retries TIMEOUT, even though its status is 0", () => {
    expect(shouldRetry(0, err("TIMEOUT", 0))).toBe(false);
    expect(shouldRetry(1, err("TIMEOUT", 0))).toBe(false);
    expect(shouldRetry(2, err("TIMEOUT", 0))).toBe(false);
  });

  it("retries NETWORK_ERROR up to the limit", () => {
    expect(shouldRetry(0, err("NETWORK_ERROR", 0))).toBe(true);
    expect(shouldRetry(1, err("NETWORK_ERROR", 0))).toBe(true);
    expect(shouldRetry(2, err("NETWORK_ERROR", 0))).toBe(false);
    expect(shouldRetry(3, err("NETWORK_ERROR", 0))).toBe(false);
  });

  it("retries transient HTTP 5xx up to the limit", () => {
    for (const status of [500, 502, 503]) {
      expect(shouldRetry(0, err(`HTTP_${status}`, status))).toBe(true);
      expect(shouldRetry(1, err(`HTTP_${status}`, status))).toBe(true);
      expect(shouldRetry(2, err(`HTTP_${status}`, status))).toBe(false);
    }
  });

  it("never retries ordinary 4xx responses", () => {
    for (const [code, status] of [
      ["UNAUTHORIZED", 401],
      ["FORBIDDEN", 403],
      ["NOT_FOUND", 404],
      ["INVALID_FILTER", 400],
      ["RATE_LIMITED", 429],
    ] as Array<[string, number]>) {
      expect(shouldRetry(0, err(code, status))).toBe(false);
      expect(shouldRetry(1, err(code, status))).toBe(false);
    }
  });

  it("respects the retry limit for retryable errors", () => {
    expect(shouldRetry(0, err("HTTP_500", 500))).toBe(true);
    expect(shouldRetry(1, err("HTTP_500", 500))).toBe(true);
    expect(shouldRetry(2, err("HTTP_500", 500))).toBe(false);
    expect(shouldRetry(5, err("HTTP_500", 500))).toBe(false);
    // Unknown non-ApiError failures keep the previous fail-open behavior.
    expect(shouldRetry(0, new Error("boom"))).toBe(true);
    expect(shouldRetry(2, new Error("boom"))).toBe(false);
  });
});
