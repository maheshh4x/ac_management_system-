/**
 * Phase 8.2 — component tests for the asset create/edit form.
 *
 * Two things are under test, because they are the two ways this form can lie
 * to the user:
 *
 *  1. **client validation** (zod): a bad value must surface under its input
 *     and must never reach the network;
 *  2. **server error mapping**: Flask answers with the envelope
 *     `{ success:false, error:{ code, message, details? } }` and the form has
 *     to place `details[]` / `SERIAL_EXISTS` / `ASSET_EXISTS` / `INVALID_ROOM`
 *     under the offending input while anything else becomes a banner.
 *
 * The API layer is exercised for real (`api()` → `fetch` → `ApiError`); only
 * `fetch` and the Supabase session lookup are stubbed, so no test can reach
 * the backend or the network.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssetFormModal } from "./AssetFormModal";
import type { ExtendedAC } from "@/lib/types/asset";

// The API client attaches a Supabase bearer token; there is no session in jsdom.
vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: null } }),
      refreshSession: async () => ({ data: { session: null } }),
      signOut: async () => ({ error: null }),
    },
  }),
}));

interface RecordedCall {
  url: string;
  method: string;
  body?: unknown;
}

type Responder = (call: RecordedCall) => Response;

const calls: RecordedCall[] = [];
let responder: Responder = () => jsonResponse({ ac_id: "AC-001" });

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req_test" },
  });
}

function ok(data: unknown, status = 200): Response {
  return jsonResponse({ success: true, data, request_id: "req_test" }, status);
}

function fail(code: string, message: string, status: number, details?: unknown): Response {
  return jsonResponse(
    { success: false, error: { code, message, details, request_id: "req_test" } },
    status,
  );
}

const CAMPUS_TREE = {
  colleges: [
    {
      college_id: "col-1",
      college_name: "Test College",
      blocks: [
        {
          block_id: "blk-1",
          college_id: "col-1",
          block_name: "Block A",
          layout: null,
          photos: [],
          floors: [
            {
              floor_id: "flr-1",
              block_id: "blk-1",
              floor_name: "Floor 1",
              floor_number: 1,
              rooms: [
                {
                  room_id: "rm-1",
                  floor_id: "flr-1",
                  department_id: null,
                  room_name: "Room 101",
                  room_type: null,
                },
              ],
            },
          ],
        },
      ],
    },
  ],
  orphan_blocks: [],
  departments: [],
};

const EDIT_ASSET: ExtendedAC = {
  id: "AC-001",
  acId: "AC-001",
  make: "Daikin",
  model: "FTKM50",
  capacity: "1.5 Ton",
  capacityTon: 1.5,
  serialNumber: "SN-001",
  type: "Split",
  status: "Working",
  location: { buildingId: "blk-1", floorId: "flr-1", roomId: "rm-1" },
  installationYear: "2021",
  remarks: "kept",
};

interface RenderProps {
  mode?: "create" | "edit";
  asset?: ExtendedAC | null;
  onClose?: () => void;
  onSaved?: () => void;
  isOpen?: boolean;
}

let queryClient: QueryClient | null = null;

function renderModal(props: RenderProps = {}) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AssetFormModal
        isOpen={props.isOpen ?? true}
        onClose={props.onClose ?? (() => {})}
        mode={props.mode ?? "create"}
        asset={props.asset ?? null}
        onSaved={props.onSaved ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

/** Submit through the visible footer button, like a user would. */
function submitCreate(): void {
  fireEvent.click(screen.getByRole("button", { name: /Create Asset/ }));
}

function inputByLabel(label: string): HTMLInputElement {
  const node = screen.getByText(label);
  return node.nextElementSibling as HTMLInputElement;
}

function postCalls(): RecordedCall[] {
  return calls.filter(c => c.method === "POST" && c.url.endsWith("/api/v1/assets"));
}

beforeEach(() => {
  calls.length = 0;
  responder = call =>
    call.method === "GET" && call.url.includes("/api/v1/campus/tree")
      ? ok(CAMPUS_TREE)
      : ok({ ac_id: "AC-001" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: RecordedCall = {
        url: String(input),
        method: (init?.method ?? "GET").toUpperCase(),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      calls.push(call);
      return responder(call);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  queryClient?.clear();
  queryClient = null;
});

describe("AssetFormModal — client validation", () => {
  it("renders nothing while the modal is closed", () => {
    const { container } = renderModal({ isOpen: false });
    expect(container.firstChild).toBeNull();
  });

  it("refuses to submit without an AC ID and never calls the API", () => {
    renderModal();
    submitCreate();

    expect(screen.getByText("AC ID is required")).toBeTruthy();
    expect(postCalls()).toHaveLength(0);
  });

  it("validates numeric ranges and keeps the other fields quiet", () => {
    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    fireEvent.change(inputByLabel("Capacity (Ton)"), { target: { value: "abc" } });
    submitCreate();

    expect(screen.getByText("Capacity must be a positive number")).toBeTruthy();
    expect(screen.queryByText("AC ID is required")).toBeNull();
    expect(postCalls()).toHaveLength(0);
  });

  it("bounds the star rating to 1–5 before posting", () => {
    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    fireEvent.change(inputByLabel("Star Rating (1–5)"), { target: { value: "9" } });
    submitCreate();

    expect(screen.getByText("Star rating must be 1–5")).toBeTruthy();
    expect(postCalls()).toHaveLength(0);
  });

  it("clears a field error as soon as the user edits that field", () => {
    renderModal();
    submitCreate();
    expect(screen.getByText("AC ID is required")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    expect(screen.queryByText("AC ID is required")).toBeNull();
  });
});

describe("AssetFormModal — server error mapping", () => {
  it("renders VALIDATION_ERROR details under the input named in loc", async () => {
    responder = call =>
      call.method === "POST"
        ? fail(
            "VALIDATION_ERROR",
            "Payload failed validation.",
            400,
            [
              { loc: ["ac_capacity_ton"], msg: "Must be between 0.5 and 10" },
              { loc: ["ac_serial_number"], msg: "Serial is already registered" },
            ],
          )
        : ok(CAMPUS_TREE);

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    const capacityWrapper = inputByLabel("Capacity (Ton)").parentElement as HTMLElement;
    await waitFor(() =>
      expect(capacityWrapper.textContent).toContain("Must be between 0.5 and 10"),
    );

    const serialWrapper = screen.getByText("Serial Number").parentElement as HTMLElement;
    expect(serialWrapper.textContent).toContain("Serial is already registered");
    // Field errors must not also be duplicated into the banner.
    expect(screen.queryByText("Payload failed validation.")).toBeNull();
  });

  it("maps SERIAL_EXISTS onto the serial number input", async () => {
    responder = call =>
      call.method === "POST"
        ? fail("SERIAL_EXISTS", "Serial SN-001 is already registered.", 409)
        : ok(CAMPUS_TREE);

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    const serialWrapper = screen.getByText("Serial Number").parentElement as HTMLElement;
    await waitFor(() =>
      expect(serialWrapper.textContent).toContain("Serial SN-001 is already registered."),
    );
    expect(screen.queryByText("Serial SN-001 is already registered.")).toBeTruthy();
  });

  it("maps ASSET_EXISTS onto the AC ID input", async () => {
    responder = call =>
      call.method === "POST"
        ? fail("ASSET_EXISTS", "AC-001 already exists.", 409)
        : ok(CAMPUS_TREE);

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    await waitFor(() => expect(screen.getByText("AC-001 already exists.")).toBeTruthy());
    // …and not as a banner, because the message belongs to a field.
    const alert = screen.queryByText(/AC-001 already exists\./);
    expect(alert?.tagName).toBe("P");
  });

  it("maps INVALID_ROOM onto the room select", async () => {
    responder = call =>
      call.method === "POST"
        ? fail("INVALID_ROOM", "That room no longer exists.", 400)
        : ok(CAMPUS_TREE);

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    const roomWrapper = screen.getByText("Room").parentElement as HTMLElement;
    await waitFor(() => expect(roomWrapper.textContent).toContain("That room no longer exists."));
  });

  it("falls back to a banner for errors that belong to no field", async () => {
    responder = call =>
      call.method === "POST"
        ? fail("FORBIDDEN", "You lack CREATE_ASSET.", 403)
        : ok(CAMPUS_TREE);

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    await waitFor(() => expect(screen.getByText("You lack CREATE_ASSET.")).toBeTruthy());
    expect(screen.queryByText("AC ID is required")).toBeNull();
  });

  it("surfaces a transport failure as a readable banner, not a crash", async () => {
    responder = () => {
      throw new TypeError("Failed to fetch");
    };

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "AC-001" },
    });
    submitCreate();

    await waitFor(() => expect(screen.getByText("Could not reach the server.")).toBeTruthy());
  });
});

describe("AssetFormModal — submit", () => {
  it("posts a normalized payload, reports the save and closes", async () => {
    const onSaved = vi.fn();
    const onClose = vi.fn();

    renderModal({ onSaved, onClose });
    fireEvent.change(screen.getByPlaceholderText("e.g. AC-001"), {
      target: { value: "  AC-001  " },
    });
    fireEvent.change(inputByLabel("Capacity (Ton)"), { target: { value: "1.5" } });
    fireEvent.change(inputByLabel("Manufacturing Year"), { target: { value: "2021" } });
    fireEvent.change(inputByLabel("Star Rating (1–5)"), { target: { value: "4" } });
    fireEvent.change(screen.getByText("Status").nextElementSibling as HTMLSelectElement, {
      target: { value: "Fault" },
    });
    submitCreate();

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onClose).toHaveBeenCalledTimes(1);

    const posted = postCalls();
    expect(posted).toHaveLength(1);
    const body = posted[0].body as Record<string, unknown>;
    expect(body.ac_id).toBe("AC-001"); // trimmed
    expect(body.current_status).toBe("not working"); // UI → backend vocabulary
    expect(body.ac_capacity_ton).toBe(1.5); // string → number
    expect(body.manufacturing_year).toBe(2021); // string → integer
    expect(body.ac_star_rating).toBe(4);
    expect(body.room_id).toBeNull(); // empty inputs become null, not ""
    expect(body.remarks).toBeNull();
    expect(body.power_factor).toBeNull();
  });

  it("offers the campus rooms once the tree has loaded", async () => {
    renderModal();
    const room = await screen.findByText("Block A / Floor 1 / Room 101");
    expect((room as HTMLOptionElement).value).toBe("rm-1");
  });

  it("pre-fills from the asset, locks the ID and PATCHes without ac_id", async () => {
    const onSaved = vi.fn();
    const onClose = vi.fn();

    renderModal({ mode: "edit", asset: EDIT_ASSET, onSaved, onClose });

    const acIdInput = screen.getByPlaceholderText("e.g. AC-001");
    expect((acIdInput as HTMLInputElement).value).toBe("AC-001");
    expect((acIdInput as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Edit AC-001")).toBeTruthy();

    const makeInput = screen.getByPlaceholderText("e.g. Daikin");
    expect((makeInput as HTMLInputElement).value).toBe("Daikin");
    fireEvent.change(makeInput, { target: { value: "LG" } });

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/ }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onClose).toHaveBeenCalledTimes(1);

    const patch = calls.find(c => c.method === "PATCH");
    expect(patch, "no PATCH was issued").toBeDefined();
    expect(patch!.url).toContain("/api/v1/assets/AC-001");
    expect(postCalls()).toHaveLength(0);

    const body = patch!.body as Record<string, unknown>;
    expect(body.ac_make_company).toBe("LG");
    expect(body.ac_id).toBeUndefined(); // edits never rewrite the identifier
    expect(body.ac_capacity_ton).toBe(1.5); // untouched values round-trip
    expect(body.installation_year).toBe(2021);
    expect(body.manufacturing_year).toBeNull(); // not set on this row
  });
});
