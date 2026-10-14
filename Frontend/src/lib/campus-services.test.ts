/**
 * P2 regression tests — the /api/v1/assets paginated envelope contract.
 *
 * REAL backend shape (captured from a live Flask response on the test DB,
 * see P2-global-search-regression-report.md §4):
 *   { success: true, data: [ ...rows ], pagination: {...}, request_id }
 * `data` IS the items array, `pagination` is a SIBLING key, and there is no
 * `items` key anywhere. searchAssets/getACs previously called
 * `api<Paginated<T>>()` (which returns `body.data` — the bare array) and
 * crashed with "Cannot read properties of undefined (reading 'map')" on
 * `.items.map(...)`. The fix routes them through `apiList<T>()`, the helper
 * built for exactly this envelope.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { blockIdForFloorId, getACs, invalidateCampusTreeCache, searchAssets } from "./campus-services";
import { ApiError } from "./api/client";
import type { ExtendedAC } from "@/lib/types/asset";

vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: "tok" } } }),
      refreshSession: async () => ({ data: { session: { access_token: "tok" } } }),
      signOut: async () => {},
    },
  }),
}));

// Row fixture: the real `_serialize()` key set (captured from the live
// endpoint), limited to the fields mapAsset consumes.
const assetRow = {
  ac_id: "AC-0007",
  asset_uuid: "11111111-2222-3333-4444-555555555555",
  asset_code: null,
  visual_id: "NITTTR-CHENNAI-B1-1F-ECE-102-A1B2",
  ac_make_company: "Voltas",
  ac_model_number: "V3D",
  ac_capacity_ton: 2,
  ac_type: "Split",
  operational_status: "WORKING",
  lifecycle_status: "ACTIVE",
  is_active: true,
  room_id: "ROOM-102",
  room_name: "ECE Lab",
  room_type: "Laboratory",
  floor_id: "F1",
  floor_name: "First Floor",
  block_id: "B1",
  block_name: "Admin Block",
  department_id: null,
  department_name: null,
  location_status: "ASSIGNED",
  location: { status: "ASSIGNED", levels: {}, manual_fields: [] },
};

const tree = {
  colleges: [
    {
      college_id: "C1",
      college_name: "NITTTR",
      blocks: [
        {
          block_id: "B1",
          college_id: "C1",
          block_name: "Admin Block",
          layout: null,
          photos: [],
          floors: [
            {
              floor_id: "F1",
              block_id: "B1",
              floor_name: "First Floor",
              floor_number: 1,
              rooms: [
                { room_id: "ROOM-102", floor_id: "F1", department_id: null, room_name: "ADM Lab", room_type: "Laboratory" },
                { room_id: "ROOM-9", floor_id: "F1", department_id: null, room_name: "Store", room_type: "Store" },
              ],
            },
          ],
        },
        { block_id: "B2", college_id: "C1", block_name: "Hostel Block", layout: null, photos: [], floors: [] },
      ],
    },
  ],
  orphan_blocks: [],
  departments: [],
};

function envelope(data: unknown, pagination = { page: 1, page_size: 20, total: 1, total_pages: 1 }) {
  return { success: true, data, pagination, request_id: "req-1" };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "req-1" },
    json: async () => body,
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchAssets — real paginated envelope (P2 regression)", () => {
  it("maps asset rows and filters buildings/rooms from the real {data, pagination} shape", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/assets?")) return jsonResponse(envelope([assetRow]));
      if (url.includes("/api/v1/campus/tree")) return jsonResponse({ success: true, data: tree });
      throw new Error("unexpected fetch: " + String(url));
    });

    const res = await searchAssets("adm");

    // The exact request GlobalSearch issues (dev default API base in tests).
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:5000/api/v1/assets?search=adm&page_size=20",
      expect.anything(),
    );
    expect(res.acs).toHaveLength(1);
    // searchAssets keeps the historical AC[] signature, but mapAsset produces
    // ExtendedAC rows at runtime (visualId etc.) — that is what the UI reads.
    const ac = res.acs[0] as ExtendedAC;
    expect(ac.visualId).toBe("NITTTR-CHENNAI-B1-1F-ECE-102-A1B2");
    expect(ac.make).toBe("Voltas");
    expect(ac.model).toBe("V3D");
    expect(ac.location).toEqual({
      buildingId: "B1",
      floorId: "F1",
      departmentId: undefined,
      roomId: "ROOM-102",
    });
    // Text filters: "admin block b1" matches, "hostel block b2" does not;
    // "adm lab room-102" matches, "store room-9" does not.
    expect(res.buildings.map(b => b.id)).toEqual(["B1"]);
    expect(res.rooms.map(r => r.id)).toEqual(["ROOM-102"]);
  });

  it("returns an empty asset list for a legitimate empty result without throwing", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/assets?")) {
        return jsonResponse(envelope([], { page: 1, page_size: 20, total: 0, total_pages: 1 }));
      }
      return jsonResponse({ success: true, data: tree });
    });

    const res = await searchAssets("zzz-no-match");
    expect(res.acs).toEqual([]);
    expect(res.buildings).toEqual([]);
    expect(res.rooms).toEqual([]);
  });

  it("surfaces a backend error envelope as a rejected ApiError, never as empty results", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ success: false, error: { code: "INVALID_PAGINATION", message: "bad page" } }, 400),
    );

    await expect(searchAssets("adm")).rejects.toBeInstanceOf(ApiError);
    await expect(searchAssets("adm")).rejects.toMatchObject({ code: "INVALID_PAGINATION" });
  });

  it("rejects (controlled) when a 200 response carries an unexpected shape", async () => {
    // `data` must be the items ARRAY. An object here is a broken contract and
    // must surface as a rejection to the caller — never as silent garbage or
    // a fabricated empty result.
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/assets?")) return jsonResponse(envelope({ items: [assetRow] }));
      return jsonResponse({ success: true, data: tree });
    });

    await expect(searchAssets("adm")).rejects.toThrow();
  });
});

describe("getACs — same envelope fix (twin call site)", () => {
  it("maps room-filtered rows from the real envelope", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(envelope([assetRow])));

    const acs = await getACs("ROOM-102");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:5000/api/v1/assets?room=ROOM-102&page_size=100",
      expect.anything(),
    );
    expect(acs).toHaveLength(1);
    expect(acs[0].id).toBe("11111111-2222-3333-4444-555555555555");
    expect(acs[0].status).toBe("Working");
  });

  it("returns [] without fetching when roomId is empty", async () => {
    await expect(getACs("")).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("blockIdForFloorId — D4-safe parent derivation", () => {
  const scopedTree = {
    colleges: [
      {
        college_id: "C1",
        college_name: "N",
        blocks: [
          {
            block_id: "BLOCK-C1-ADMIN-BLOCK",
            college_id: "C1",
            block_name: "Admin Block",
            layout: null,
            photos: [],
            floors: [
              {
                floor_id: "BLOCK-C1-ADMIN-BLOCK-FLR-GROUND",
                block_id: "BLOCK-C1-ADMIN-BLOCK",
                floor_name: "Ground",
                floor_number: 0,
                rooms: [],
              },
            ],
          },
        ],
      },
    ],
    orphan_blocks: [],
    departments: [],
  };

  function serveTree(activeTree: unknown) {
    invalidateCampusTreeCache();
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/campus/tree")) return jsonResponse({ success: true, data: activeTree });
      throw new Error("unexpected fetch: " + String(url));
    });
  }

  it("resolves a college-scoped block id from the tree", async () => {
    serveTree(scopedTree);
    // Dash surgery would return "BLOCK-C1" here — the lookup must not.
    await expect(blockIdForFloorId("BLOCK-C1-ADMIN-BLOCK-FLR-GROUND")).resolves.toBe(
      "BLOCK-C1-ADMIN-BLOCK",
    );
  });

  it("falls back to the legacy derivation when the floor is absent", async () => {
    serveTree(scopedTree);
    await expect(blockIdForFloorId("NOPE-FLR-X")).resolves.toBe("NOPE-FLR");
    await expect(blockIdForFloorId("")).resolves.toBe("");
  });
});
