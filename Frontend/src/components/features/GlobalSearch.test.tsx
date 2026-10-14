/**
 * P2 regression tests — GlobalSearch lifecycle on /admin/dashboard.
 *
 * Covers: debounced requests, mapped rendering, empty results, controlled
 * error state (no unhandled promise rejections), stale-response discard
 * (an older query can never overwrite a newer one), unmount safety and the
 * preserved navigation behaviour.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalSearch } from "./GlobalSearch";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  setState: vi.fn(),
  searchAssets: vi.fn(),
  blockIdForFloorId: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/contexts/CampusContext", () => ({ useCampus: () => ({ setState: mocks.setState }) }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "VIEWER" } }) }));
vi.mock("@/lib/campus-services", () => ({
  searchAssets: (q: string) => mocks.searchAssets(q),
  blockIdForFloorId: (floorId: string) => mocks.blockIdForFloorId(floorId),
}));

afterEach(() => {
  cleanup();
  mocks.searchAssets.mockReset();
  mocks.blockIdForFloorId.mockReset();
  mocks.push.mockReset();
  mocks.setState.mockReset();
});

const RESULT_A = {
  acs: [
    {
      id: "a1",
      make: "AlphaCorp",
      model: "M1",
      visualId: "VID-A",
      status: "Working",
      location: { buildingId: "B1", floorId: "F1", roomId: "R1" },
    },
  ],
  rooms: [],
  buildings: [],
};

const RESULT_B = {
  acs: [
    {
      id: "b1",
      make: "BetaCorp",
      model: "M2",
      visualId: "VID-B",
      status: "Fault",
      location: { buildingId: "B2", floorId: "F2", roomId: "R2" },
    },
  ],
  rooms: [],
  buildings: [],
};

const EMPTY = { acs: [], rooms: [], buildings: [] };

function typeQuery(value: string) {
  const input = document.querySelector('input[type="text"]') as HTMLInputElement;
  fireEvent.change(input, { target: { value } });
}

function spinnerVisible(): boolean {
  return document.querySelector(".animate-spin") !== null;
}

describe("GlobalSearch — P2 lifecycle regression", () => {
  it("debounces input, then renders mapped results for the query", async () => {
    mocks.searchAssets.mockResolvedValue(RESULT_A);
    render(<GlobalSearch />);

    typeQuery("adm");
    // Not fired yet: the 300 ms debounce has not elapsed.
    expect(mocks.searchAssets).not.toHaveBeenCalled();

    await screen.findByText(/AlphaCorp/);
    expect(mocks.searchAssets).toHaveBeenCalledTimes(1);
    expect(mocks.searchAssets).toHaveBeenCalledWith("adm");
  });

  it("renders an empty result set without throwing and clears the spinner", async () => {
    mocks.searchAssets.mockResolvedValue(EMPTY);
    render(<GlobalSearch />);

    typeQuery("zzz-no-match");
    await waitFor(() => expect(mocks.searchAssets).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(spinnerVisible()).toBe(false));
    expect(screen.queryByText("AC Units")).toBeNull();
  });

  it("shows a recoverable error, clears the spinner, and produces no unhandled rejection", async () => {
    const unhandled: unknown[] = [];
    const track = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", track);
    try {
      mocks.searchAssets.mockRejectedValue(new Error("network down"));
      render(<GlobalSearch />);

      typeQuery("adm");
      const error = await screen.findByTestId("global-search-error");
      expect(error.textContent).toMatch(/unavailable/i);
      await waitFor(() => expect(spinnerVisible()).toBe(false));

      // Drain the microtask queue: any escaping rejection would be tracked
      // (and vitest itself fails the run on unhandled rejections).
      await new Promise(resolve => setTimeout(resolve, 30));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", track);
    }
  });

  it("never lets an older response overwrite a newer query's results", async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    mocks.searchAssets
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve; }));

    render(<GlobalSearch />);
    typeQuery("ad");
    await waitFor(() => expect(mocks.searchAssets).toHaveBeenCalledTimes(1));
    typeQuery("admin");
    await waitFor(() => expect(mocks.searchAssets).toHaveBeenCalledTimes(2));

    // The NEWER query resolves first…
    resolveSecond(RESULT_B);
    await screen.findByText(/BetaCorp/);

    // …then the OLDER, stale response arrives late: it must be discarded.
    resolveFirst(RESULT_A);
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(screen.queryByText(/AlphaCorp/)).toBeNull();
    expect(screen.queryByText(/BetaCorp/)).not.toBeNull();
    expect(spinnerVisible()).toBe(false);
  });

  it("skips the request and clears state for queries shorter than 2 characters", async () => {
    render(<GlobalSearch />);
    typeQuery("a");
    await new Promise(resolve => setTimeout(resolve, 350));
    expect(mocks.searchAssets).not.toHaveBeenCalled();
    expect(document.querySelector(".absolute.top-full")).toBeNull();
  });

  it("discards an in-flight response after unmount without crashing", async () => {
    let resolveSearch!: (value: unknown) => void;
    mocks.searchAssets.mockImplementation(() => new Promise(resolve => { resolveSearch = resolve; }));

    const { unmount } = render(<GlobalSearch />);
    typeQuery("adm");
    await waitFor(() => expect(mocks.searchAssets).toHaveBeenCalledTimes(1));
    unmount();

    // Late resolution after unmount must be a no-op (sequence guard).
    resolveSearch(RESULT_A);
    await new Promise(resolve => setTimeout(resolve, 30));
  });

  it("preserves selection behaviour: navigates to the campus map with the AC context", async () => {
    mocks.searchAssets.mockResolvedValue(RESULT_A);
    render(<GlobalSearch />);

    typeQuery("adm");
    const label = await screen.findByText(/AlphaCorp/);
    fireEvent.click(label.closest("button")!);

    expect(mocks.push).toHaveBeenCalledWith("/campus");
    await waitFor(
      () => expect(mocks.setState).toHaveBeenCalledWith(
        expect.objectContaining({ level: "AC", selectedAcId: "a1" }),
      ),
      { timeout: 1500 }, // selection dispatch waits 500 ms for the map mount
    );
  });

  it("derives the room's building from the floor lookup, never dash surgery", async () => {
    // D4 college-scoped ids: first-two-segments surgery would yield
    // "BLOCK-C1" instead of the real building id.
    const RESULT_ROOM = {
      acs: [],
      rooms: [
        {
          id: "BLOCK-C1-ADMIN-BLOCK-FLR-GROUND-RM-101",
          name: "Room 101",
          type: "Lab",
          floorId: "BLOCK-C1-ADMIN-BLOCK-FLR-GROUND",
        },
      ],
      buildings: [],
    };
    mocks.searchAssets.mockResolvedValue(RESULT_ROOM);
    mocks.blockIdForFloorId.mockResolvedValue("BLOCK-C1-ADMIN-BLOCK");
    render(<GlobalSearch />);

    typeQuery("room 101");
    const label = await screen.findByText(/Room 101/);
    fireEvent.click(label.closest("button")!);

    expect(mocks.push).toHaveBeenCalledWith("/campus");
    await waitFor(
      () => expect(mocks.blockIdForFloorId).toHaveBeenCalledWith("BLOCK-C1-ADMIN-BLOCK-FLR-GROUND"),
      { timeout: 1500 }, // selection dispatch waits 500 ms for the map mount
    );
    await waitFor(
      () => expect(mocks.setState).toHaveBeenCalledWith(
        expect.objectContaining({
          level: "Room",
          selectedBuildingId: "BLOCK-C1-ADMIN-BLOCK",
          selectedFloorId: "BLOCK-C1-ADMIN-BLOCK-FLR-GROUND",
          selectedRoomId: "BLOCK-C1-ADMIN-BLOCK-FLR-GROUND-RM-101",
        }),
      ),
      { timeout: 1500 },
    );
  });
});
