/**
 * Assignable-technicians hook tests: the Assign Job modal's dropdown is fed
 * by GET /api/v1/users/assignable-technicians (ASSIGN_MAINTENANCE) — not the
 * MANAGE_USERS-only /users list, which leaves managers with an empty menu.
 * Covers the exact path, response parsing (hulala shape), and the visible
 * error state the modal renders on failure.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./client";
import { useAssignableTechnicians } from "./users";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, api: vi.fn() };
});

const mockedApi = vi.mocked(api);

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAssignableTechnicians", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls the least-privilege endpoint and parses the technician list", async () => {
    mockedApi.mockResolvedValue({
      technicians: [
        { id: "79276688-d410-4867-a272-0b8d5c13e6e3", name: "hulala", email: "kungfuavatar8@gmail.com" },
      ],
      total: 1,
    });
    const { result } = renderHook(() => useAssignableTechnicians(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedApi).toHaveBeenCalledWith("/api/v1/users/assignable-technicians");
    expect(result.current.data?.technicians).toEqual([
      { id: "79276688-d410-4867-a272-0b8d5c13e6e3", name: "hulala", email: "kungfuavatar8@gmail.com" },
    ]);
  });

  it("surfaces API errors so the modal can render them instead of an empty menu", async () => {
    mockedApi.mockRejectedValue(new ApiError("FORBIDDEN", "Forbidden.", 403, null));
    const { result } = renderHook(() => useAssignableTechnicians(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});
