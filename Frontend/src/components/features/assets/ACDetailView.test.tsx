/**
 * Regression test for the empty-location detail page.
 *
 * An AC with a RESOLVED location must display the intended mapping:
 *   Building = block_name, Floor = floor_name,
 *   Room = room_type (falling back to room_name only when no type exists),
 *   Department = department_name.
 *
 * The whole path is exercised for real — fetch → envelope unwrap → zod →
 * `mapAsset` → component — only router/auth/dynamic/jobs are stubbed, so a
 * mapping break anywhere between the API response and the InfoCard fails here.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACDetailView } from "./ACDetailView";

vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: null } }),
      refreshSession: async () => ({ data: { session: null } }),
      signOut: async () => ({ error: null }),
    },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href?: string }) => (
    <a href={href}>{children}</a>
  ),
}));

// Never load the three.js viewer in jsdom (it is already code-split in the app).
vi.mock("next/dynamic", () => ({ default: () => () => null }));

const authState = vi.hoisted(() => ({ user: null as null | { role: string } }));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: authState.user }),
}));

vi.mock("@/lib/api/maintenance", () => ({
  useJobs: () => ({ jobs: [] }),
}));

/** Resolved location exactly as GET /api/v1/assets/:id returns it. */
const RESOLVED_DETAIL = {
  ac_id: "AC-LOC-TEST-RESOLVED-1",
  asset_uuid: "123e4567-e89b-12d3-a456-426614174000",
  asset_code: "AC-LOC-TEST-RESOLVED-1",
  room_id: "ROOM-102",
  room_name: "CSE Computer Lab 1",
  room_type: "Workshop",
  floor_id: "MAIN-ACADEMIC-BLOCK-FLR-1",
  floor_name: "Floor 1",
  block_id: "MAIN-ACADEMIC-BLOCK",
  block_name: "Main Academic Block",
  department_id: "DEPT-COMPUTER-SCIENCE",
  department_name: "Computer Science",
  ac_make_company: "Daikin",
  ac_model_number: "FTKM50",
  ac_serial_number: "SN-1",
  ac_capacity_ton: 1.5,
  ac_type: "Split",
  operational_status: "WORKING",
  location_status: "ASSIGNED",
  // Nested location exactly as GET /api/v1/assets/:id returns it — every
  // level RESOLVED, so the §15 review strip must stay hidden.
  location: {
    status: "ASSIGNED",
    levels: {
      college: {
        id: "NITTTR-CHENNAI",
        name: "National Institute of Technical Teachers Training and Research",
        status: "RESOLVED",
        source: "National Institute of Technical Teachers Training and Research",
      },
      block: {
        id: "MAIN-ACADEMIC-BLOCK",
        name: "Main Academic Block",
        status: "RESOLVED",
        source: "Main Academic Block",
      },
      floor: { id: "MAIN-ACADEMIC-BLOCK-FLR-1", name: "Floor 1", status: "RESOLVED", source: "Floor 1" },
      department: {
        id: "DEPT-COMPUTER-SCIENCE",
        name: "Computer Science",
        status: "RESOLVED",
        source: "Computer Science",
      },
      room: { id: "ROOM-102", name: "CSE Computer Lab 1", status: "RESOLVED", source: "CSE Computer Lab 1", type: "Workshop" },
    },
    manual_fields: [],
  },
  lifecycle_status: "ACTIVE",
  is_active: true,
  qr_version: 1,
  maintenance_history: [],
  movement_history: [],
  location_history: [],
  attributes: [],
};

/**
 * §15 PARTIAL state: the chain confirmed, the room left UNRESOLVED with the
 * source text the import supplied preserved verbatim.
 */
const PARTIAL_DETAIL = {
  ...RESOLVED_DETAIL,
  ac_id: "AC-LOC-TEST-PARTIAL-1",
  asset_code: "AC-LOC-TEST-PARTIAL-1",
  room_id: null,
  room_name: null,
  room_type: null,
  location_status: "PARTIAL",
  location: {
    status: "PARTIAL",
    levels: {
      college: {
        id: "NITTTR-CHENNAI",
        name: "National Institute of Technical Teachers Training and Research",
        status: "RESOLVED",
        source: "National Institute of Technical Teachers Training and Research",
      },
      block: { id: "MAIN-ACADEMIC-BLOCK", name: "Main Academic Block", status: "RESOLVED", source: "Main Academic Block" },
      floor: { id: "MAIN-ACADEMIC-BLOCK-FLR-1", name: "Floor 1", status: "RESOLVED", source: "Floor 1" },
      department: { id: "DEPT-COMPUTER-SCIENCE", name: "Computer Science", status: "RESOLVED", source: "Computer Science" },
      room: { id: null, name: null, status: "UNRESOLVED", source: "Imaginary Room 404", type: null },
    },
    manual_fields: [],
  },
};

/** An ambiguous room (two canonical matches) — never auto-assigned. */
const AMBIGUOUS_DETAIL = {
  ...PARTIAL_DETAIL,
  ac_id: "AC-LOC-TEST-AMBIG-1",
  asset_code: "AC-LOC-TEST-AMBIG-1",
  location: {
    status: "PARTIAL",
    levels: {
      college: {
        id: "NITTTR-CHENNAI",
        name: "National Institute of Technical Teachers Training and Research",
        status: "RESOLVED",
        source: "National Institute of Technical Teachers Training and Research",
      },
      block: { id: "MAIN-ACADEMIC-BLOCK", name: "Main Academic Block", status: "RESOLVED", source: "Main Academic Block" },
      floor: { id: "MAIN-ACADEMIC-BLOCK-FLR-1", name: "Floor 1", status: "RESOLVED", source: "Floor 1" },
      department: { id: "DEPT-ADMINISTRATION", name: "Administration", status: "RESOLVED", source: "Administration" },
      room: { id: null, name: null, status: "AMBIGUOUS", source: "CSE Computer Lab 1", type: null },
    },
    manual_fields: [],
  },
};

/** Swapped in per test — the fetch stub serves whatever is current. */
let activeDetail: Record<string, unknown> = RESOLVED_DETAIL;

beforeEach(() => {
  activeDetail = RESOLVED_DETAIL;
  authState.user = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/api/v1/assets/")
        ? { success: true, data: activeDetail, request_id: "req_test" }
        : { success: true, data: [], request_id: "req_test" };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json", "X-Request-ID": "req_test" },
      });
    }),
  );
  // AcQrActions previews via object URLs (absent in jsdom).
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:qr"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ACDetailView acId={RESOLVED_DETAIL.asset_uuid} backHref="/admin/assets" />
    </QueryClientProvider>,
  );
}

describe("ACDetailView — location card", () => {
  it("displays block, floor, room type and department for a resolved location", async () => {
    renderDetail();

    expect(await screen.findByText("Main Academic Block")).toBeTruthy(); // Building
    expect(await screen.findByText("Floor 1")).toBeTruthy(); // Floor
    expect(await screen.findByText("Workshop")).toBeTruthy(); // Room = room_type
    expect(await screen.findByText("Computer Science")).toBeTruthy(); // Department
  });

  it("prefers room_type over room_name (intended mapping)", async () => {
    renderDetail();

    await screen.findByText("Workshop");
    expect(screen.queryByText("CSE Computer Lab 1")).toBeNull();
  });
});

describe("ACDetailView — location review strip (§15)", () => {
  it("stays hidden when every level is RESOLVED", async () => {
    renderDetail();

    await screen.findByText("Main Academic Block");
    expect(screen.queryByText("Location needs review")).toBeNull();
  });

  it("lists a level whose preserved source matched nothing (UNRESOLVED)", async () => {
    activeDetail = PARTIAL_DETAIL;
    renderDetail();

    expect(await screen.findByText("Location needs review")).toBeTruthy();
    // Level label + the quoted source text kept verbatim + per-field chip.
    expect(await screen.findByText("room")).toBeTruthy();
    // Twice: the review strip AND the Room grid cell (room_id NULL no
    // longer hides the preserved source there).
    expect(await screen.findAllByText(/Imaginary Room 404/)).toHaveLength(2);
    expect(await screen.findByText("UNRESOLVED")).toBeTruthy();
    // Confirmed levels are not queued for review...
    expect(screen.queryByText("block")).toBeNull();
    // ...and the confirmed chain still renders in the grid.
    expect(await screen.findByText("Main Academic Block")).toBeTruthy();
    expect(await screen.findByText("Floor 1")).toBeTruthy();
  });

  it("flags an AMBIGUOUS level distinctly from an unresolved one", async () => {
    activeDetail = AMBIGUOUS_DETAIL;
    renderDetail();

    expect(await screen.findByText("Location needs review")).toBeTruthy();
    expect(await screen.findByText("AMBIGUOUS")).toBeTruthy();
    // Twice again: strip + Room grid cell surface the ambiguous text.
    expect(await screen.findAllByText(/CSE Computer Lab 1/)).toHaveLength(2);
    expect(screen.queryByText("UNRESOLVED")).toBeNull();
  });

  it("renders source-backed values when room_id is NULL (PARTIAL, §7)", async () => {
    // block_id/floor_id/room_id NULL, department resolved, source text kept:
    // room_id NULL must not hide the other available dimensions.
    activeDetail = {
      ...RESOLVED_DETAIL,
      ac_id: "AC-LOC-TEST-SOURCE-1",
      asset_code: "AC-LOC-TEST-SOURCE-1",
      room_id: null,
      room_name: null,
      room_type: "ADMIN OFFICE",
      floor_id: null,
      floor_name: null,
      block_id: null,
      block_name: "Phantom Block",
      department_id: "DEPT-ADMINISTRATION",
      department_name: "Administration",
      location_status: "PARTIAL",
      location: {
        status: "PARTIAL",
        levels: {
          college: { id: null, name: null, status: "MISSING", source: null },
          block: { id: null, name: null, status: "UNRESOLVED", source: "Phantom Block" },
          floor: { id: null, name: null, status: "MISSING", source: null },
          department: {
            id: "DEPT-ADMINISTRATION",
            name: "Administration",
            status: "RESOLVED",
            source: "Administration",
          },
          room: { id: null, name: null, status: "MISSING", source: null, type: null },
        },
        manual_fields: [],
      },
    };
    renderDetail();

    expect(await screen.findByText("Phantom Block")).toBeTruthy(); // Building from source
    expect(await screen.findByText("Administration")).toBeTruthy(); // Department confirmed
    expect(await screen.findByText("ADMIN OFFICE")).toBeTruthy(); // Room = room_type, no room
  });
});

describe("ACDetailView — visual asset ID", () => {
  it("shows the derived Visual ID as the primary identifier (UUID demoted)", async () => {
    activeDetail = { ...RESOLVED_DETAIL, visual_id: "NC-MA-1F-CS-CCL1-0CA6C87C" };
    renderDetail();

    // Twice: breadcrumb trail AND the detail heading both lead with it.
    expect(await screen.findAllByText("NC-MA-1F-CS-CCL1-0CA6C87C")).toHaveLength(2);
  });
});

describe("ACDetailView — asset QR card", () => {  it("shows no QR surface without a permitted role", async () => {
    renderDetail();

    await screen.findByText("Main Academic Block");
    expect(screen.queryByText("Asset QR")).toBeNull();
    expect(screen.queryByText("Download QR")).toBeNull();
  });

  it("shows the QR card with Download QR for SUPER_ADMIN", async () => {
    authState.user = { role: "SUPER_ADMIN" };
    renderDetail();

    expect(await screen.findByText("Asset QR")).toBeTruthy();
    expect(await screen.findByText("Download QR")).toBeTruthy();
  });
});
