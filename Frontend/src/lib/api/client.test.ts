/**
 * API timeout contract (§13): the normal budget is 15 s everywhere, while
 * bulk import opts into a caller-specific 120 s via apiFormData's timeoutMs
 * (no global increase, no blind retries — a timed-out commit is verified
 * and replayed with the same content-derived Idempotency-Key).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, apiFormData, IMPORT_TIMEOUT_MS } from "./client";

vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: "tok" } } }),
      refreshSession: async () => ({ data: { session: { access_token: "tok" } } }),
    },
  }),
}));

function hangingFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, init?: { signal?: AbortSignal }) => {
      const signal = init?.signal;
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {
          once: true,
        });
      });
    }),
  );
}

function okFetch(payload: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("api timeout budget", () => {
  it("aborts normal requests at 15 s with TIMEOUT", async () => {
    hangingFetch();
    const pending = api("/api/v1/me");
    const assertion = expect(pending).rejects.toMatchObject({ code: "TIMEOUT" });
    await vi.advanceTimersByTimeAsync(15_000);
    await assertion;
  });

  it("still serves fast responses without timing out", async () => {
    okFetch({ success: true, data: { id: "u1" }, request_id: "r" });
    const pending = api("/api/v1/me");
    const assertion = expect(pending).resolves.toEqual({ id: "u1" });
    await vi.advanceTimersByTimeAsync(100);
    await assertion;
  });
});

describe("apiFormData timeout budget", () => {
  it("defaults to the same 15 s budget", async () => {
    hangingFetch();
    const pending = apiFormData("/api/v1/assets/import", new FormData());
    const assertion = expect(pending).rejects.toMatchObject({ code: "TIMEOUT" });
    await vi.advanceTimersByTimeAsync(15_000);
    await assertion;
  });

  it("honours a caller-specific longer timeout (bulk import)", async () => {
    expect(IMPORT_TIMEOUT_MS).toBe(120_000);
    hangingFetch();
    const pending = apiFormData("/api/v1/assets/import", new FormData(), undefined, IMPORT_TIMEOUT_MS);
    const assertion = expect(pending).rejects.toMatchObject({ code: "TIMEOUT" });
    await vi.advanceTimersByTimeAsync(15_000);
    // Still in flight well past the normal budget...
    let settled = false;
    void pending.then(
      () => (settled = true),
      () => (settled = true),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(false);
    // ...until its own budget expires.
    await vi.advanceTimersByTimeAsync(105_000);
    await assertion;
  });
});
