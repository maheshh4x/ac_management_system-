import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";
import { formatDuration, useAssetSessions, useStartSession, useStopSession } from "./sessions";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, api: vi.fn() };
});

const mockedApi = vi.mocked(api);

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("formatDuration", () => {
  it("renders human ON-time spans", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(-5)).toBe("—");
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(90)).toBe("1m");
    expect(formatDuration(3661)).toBe("1h 1m");
  });
});

describe("usage-session hooks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists sessions for the referenced asset", async () => {
    const rows = [{ id: "s1", ac_id: "AC-1", duration_seconds: 60 }];
    mockedApi.mockResolvedValue(rows);
    const { result } = renderHook(() => useAssetSessions("AC-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedApi).toHaveBeenCalledWith("/api/v1/assets/AC-1/sessions?limit=20");
    expect(result.current.data).toEqual(rows);
  });

  it("starts and stops through the asset-scoped endpoints", async () => {
    mockedApi.mockResolvedValue({ id: "s1", ended_at: null });
    const { result: start } = renderHook(() => useStartSession("AC-1"), { wrapper });
    await start.current.mutateAsync(null);
    expect(mockedApi).toHaveBeenCalledWith("/api/v1/assets/AC-1/sessions/start", {
      method: "POST",
      body: { note: null },
    });

    mockedApi.mockResolvedValue({ id: "s1", ended_at: "2026-01-01T00:05:00+00:00" });
    const { result: stop } = renderHook(() => useStopSession("AC-1"), { wrapper });
    await stop.current.mutateAsync("done");
    expect(mockedApi).toHaveBeenCalledWith("/api/v1/assets/AC-1/sessions/stop", {
      method: "POST",
      body: { note: "done" },
    });
  });
});
