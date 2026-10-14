/**
 * Outdoor Unit QR card tests (Feature A): the linked outdoor code is a
 * distinct identity (different acId than the indoor asset); the unlinked
 * state shows no QR at all; an outdoor row points back at its parent.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OutdoorUnitQrCard } from "./OutdoorUnitQrCard";
import { fetchQrImage } from "@/lib/qr-service";
import type { AssetDetail } from "@/lib/api/assets";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "viewer", role: "VIEWER" } }),
}));

vi.mock("@/lib/qr-service", () => ({
  fetchQrImage: vi.fn(async () => new Blob(["qr-bytes"], { type: "image/png" })),
  fetchQrPayload: vi.fn(async () => "https://acms.example/a/OU-UUID-1"),
  fetchQrBatchPdf: vi.fn(async () => new Blob(["pdf"])),
  regenerateQr: vi.fn(),
  downloadBlob: vi.fn(),
}));

vi.mock("@/lib/qr-label", () => ({
  downloadQrLabel: vi.fn(async () => undefined),
}));

const BASE = {
  id: "IN-UUID-1",
  acId: "AC-1",
  visualId: "VIS-1",
  make: "Daikin",
  model: "FTKM50",
  capacity: "1.5 Ton",
  serialNumber: "SN-1",
  type: "Split",
  status: "Working",
  location: { buildingId: "B", floorId: "F", roomId: "R" },
  buildingName: "Main Block",
  floorName: "Floor 1",
  roomName: "Lab 1",
  maintenanceHistory: [],
  movementHistory: [],
  locationHistory: [],
  attributes: [],
} as unknown as AssetDetail;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:qr"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});

describe("OutdoorUnitQrCard", () => {
  it("renders a distinct outdoor QR when linked", async () => {
    render(
      <OutdoorUnitQrCard
        ac={{
          ...BASE,
          unitRole: "INDOOR",
          outdoorUnit: {
            ac_id: "AC-1-OU",
            asset_uuid: "OU-UUID-1",
            asset_code: "AC-1-OU",
            ac_make_company: "Daikin",
            ac_model_number: "FTKM50-O",
            ac_serial_number: "SN-OU-1",
            operational_status: "WORKING",
          },
        }}
        canEdit={false}
        onLink={() => undefined}
      />,
    );

    expect(await screen.findByText("Outdoor Unit")).toBeTruthy();
    // The outdoor code points at the outdoor UUID, not the indoor asset.
    expect(await screen.findByText("https://acms.example/a/OU-UUID-1")).toBeTruthy();
    expect(screen.queryByText("Outdoor Unit Not Linked")).toBeNull();
  });

  it("shows the not-linked state with no QR when unlinked", () => {
    render(
      <OutdoorUnitQrCard ac={{ ...BASE, unitRole: "INDOOR", outdoorUnit: null }} canEdit={false} onLink={() => undefined} />,
    );

    expect(screen.getByText("Outdoor Unit Not Linked")).toBeTruthy();
    expect(vi.mocked(fetchQrImage)).not.toHaveBeenCalled();
  });

  it("points an outdoor row back at its indoor parent", () => {
    render(
      <OutdoorUnitQrCard
        ac={{
          ...BASE,
          unitRole: "OUTDOOR",
          parentUnit: {
            ac_id: "AC-1",
            asset_uuid: "IN-UUID-1",
            asset_code: "AC-1",
            ac_make_company: "Daikin",
            ac_model_number: "FTKM50",
            operational_status: "WORKING",
          },
        }}
        canEdit={false}
        onLink={() => undefined}
      />,
    );

    expect(screen.getByText(/Linked indoor system/)).toBeTruthy();
    expect(screen.getByText(/AC-1/)).toBeTruthy();
  });
});
