/**
 * AcQrActions branded-download tests (§10 frontend): the primary Download QR
 * button composes the branded label from the already-loaded asset + preview
 * blob (zero additional API requests); the raw PNG/SVG actions are preserved.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AcQrActions } from "./AcQrActions";
import { downloadQrLabel } from "@/lib/qr-label";
import { fetchQrImage } from "@/lib/qr-service";
import type { ExtendedAC } from "@/lib/types/asset";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "admin", role: "SUPER_ADMIN" } }),
}));

vi.mock("@/lib/qr-service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/qr-service")>();
  return {
    ...actual,
    fetchQrImage: vi.fn(async () => new Blob(["qr-bytes"], { type: "image/png" })),
    fetchQrPayload: vi.fn(async () => "https://acms.example/a/ASSET-UUID-1"),
    fetchQrBatchPdf: vi.fn(async () => new Blob(["pdf"])),
    regenerateQr: vi.fn(async () => ({ qr_version: 2, payload: "https://acms.example/a/ASSET-UUID-1" })),
  };
});

vi.mock("@/lib/qr-label", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/qr-label")>();
  return { ...actual, downloadQrLabel: vi.fn(async () => undefined) };
});

const ASSET = {
  id: "ASSET-UUID-1",
  acId: "AC-LOC-TEST-1",
  make: "Daikin",
  model: "FTKM50",
  buildingName: "Main Academic Block",
  floorName: "Floor 1",
  roomName: null,
  roomType: "ADMIN OFFICE",
  departmentName: "Administration",
  location: { buildingId: "MAIN-ACADEMIC-BLOCK", floorId: "FLR-1", departmentId: "DEPT-ADM", roomId: "" },
  locationDetail: {
    status: "PARTIAL",
    levels: {
      college: { id: "NITTTR-CHENNAI", name: "National Institute", status: "RESOLVED", source: null },
      block: { id: "MAIN-ACADEMIC-BLOCK", name: "Main Academic Block", status: "RESOLVED", source: null },
      floor: { id: "FLR-1", name: "Floor 1", status: "RESOLVED", source: null },
      department: { id: "DEPT-ADM", name: "Administration", status: "RESOLVED", source: null },
      room: { id: null, name: null, status: "MISSING", source: null, type: null },
    },
    manual_fields: [],
  },
} as unknown as ExtendedAC;

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:qr"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});

afterEach(() => {
  cleanup();
});

describe("AcQrActions — branded Download QR", () => {
  it("composes the label from loaded data with zero extra requests", async () => {
    render(<AcQrActions acId="AC-LOC-TEST-1" asset={ASSET} />);

    const button = await screen.findByText("Download QR");
    expect(fetchQrImage).toHaveBeenCalledTimes(1); // mount preview only
    fireEvent.click(button);
    await waitFor(() => expect(downloadQrLabel).toHaveBeenCalledTimes(1));

    const [fields, blob, filename] = vi.mocked(downloadQrLabel).mock.calls[0];
    expect(fields).toMatchObject({
      assetId: "AC-LOC-TEST-1",
      make: "Daikin",
      model: "FTKM50",
      college: "National Institute",
      block: "Main Academic Block",
      department: "Administration",
      floor: "Floor 1",
      room: "ADMIN OFFICE",
    });
    expect(blob).toBeInstanceOf(Blob);
    expect(filename).toBe("QR_AC-LOC-TEST-1_label.png");
    // No refetch: the mount-time preview blob is reused.
    expect(fetchQrImage).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Branded QR label downloaded.")).toBeTruthy();
  });

  it("keeps the raw PNG download working", async () => {
    render(<AcQrActions acId="AC-LOC-TEST-1" asset={ASSET} />);

    fireEvent.click(await screen.findByText("PNG"));
    await waitFor(() => expect(fetchQrImage).toHaveBeenCalledTimes(2)); // mount + raw
    expect(await screen.findByText("QR image downloaded.")).toBeTruthy();
  });
});
