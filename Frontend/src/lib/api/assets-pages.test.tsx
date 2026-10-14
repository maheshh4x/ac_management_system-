import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiList } from "./client";
import { useAllAssets } from "./assets";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, apiList: vi.fn(), api: vi.fn() };
});

const mockedApiList = vi.mocked(apiList);

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function pageOf(ids: string[], totalPages: number) {
  return {
    items: ids.map((ac_id, i) => ({ ac_id, asset_uuid: `uuid-${ac_id}-${i}` })),
    pagination: { page: 1, page_size: 200, total: ids.length, total_pages: totalPages },
  };
}

describe("useAllAssets bounded-concurrency page walk", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("concatenates pages in ascending order", async () => {
    mockedApiList
      .mockResolvedValueOnce(pageOf(["A1", "A2"], 3))
      .mockResolvedValueOnce(pageOf(["B1"], 3))
      .mockResolvedValueOnce(pageOf(["C1", "C2"], 3));
    const { result } = renderHook(() => useAllAssets({}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.map((a) => a.acId)).toEqual(["A1", "A2", "B1", "C1", "C2"]);
    expect(mockedApiList).toHaveBeenCalledTimes(3);
  });

  it("fetches follow-up pages concurrently, not one at a time", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    mockedApiList.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 5));
      inFlight -= 1;
      return pageOf(["X"], 5);
    });
    const { result } = renderHook(() => useAllAssets({}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(maxInFlight).toBeGreaterThan(1);
    expect(mockedApiList).toHaveBeenCalledTimes(5);
  });

  it("never fetches beyond ten pages even when more exist", async () => {
    mockedApiList.mockResolvedValue(pageOf(["X"], 99));
    const { result } = renderHook(() => useAllAssets({}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedApiList).toHaveBeenCalledTimes(10);
    expect(result.current.data).toHaveLength(10);
  });

  it("fails the whole query when any page fails (no partial data as complete)", async () => {
    mockedApiList
      .mockResolvedValueOnce(pageOf(["A1"], 3))
      .mockRejectedValueOnce(new Error("page 2 exploded"))
      .mockResolvedValueOnce(pageOf(["C1"], 3));
    const { result } = renderHook(() => useAllAssets({}), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});
