/**
 * Adapter unit tests (Phase 2, task 7): fixtures prove the vocabulary mapping
 * from 01-assessment-and-decisions §4 both directions.
 */
import { describe, expect, it } from "vitest";

import {
  effectiveLocationText,
  locationLevelText,
  locationReviewLevels,
  mapAsset,
  parseCapacityTon,
  toApiStatus,
  toLocationDetail,
  toUiStatus,
  type ApiAsset,
} from "./asset";
import { mapWorkOrder, toUiJobStatus } from "./job";
import { mapMovement, toUiMovementStatus } from "./movement";
import { mapProfileToUser, toProfilePatch, toUiStatus as toUiUserStatus } from "./user";

const assetRow: ApiAsset = {
  ac_id: "NITTTR-CSE-LAB1-01",
  asset_uuid: "123e4567-e89b-12d3-a456-426614174000",
  asset_code: "NITTTR-CSE-LAB1-01",
  room_id: "R-CSE-L1",
  room_name: "Computer Lab 1",
  floor_id: "F-2",
  floor_name: "Floor 2",
  block_id: "BLK-001",
  block_name: "Academic Block A",
  department_id: "D-CSE",
  department_name: "CSE",
  ac_make_company: "Voltas",
  ac_model_number: "VW183PC",
  ac_serial_number: "VLT98765",
  ac_capacity_ton: 1.5,
  ac_type: "Split",
  current_status: "working",
  ac_star_rating: 3,
  power_rating_kw: 1.6,
  power_factor: 0.95,
  next_service_date: "2026-12-01",
  is_active: true,
};

describe("asset adapter", () => {
  it("maps the backend row to the UI domain vocabulary", () => {
    const ac = mapAsset(assetRow);
    expect(ac.id).toBe(assetRow.asset_uuid); // asset_uuid is the stable key
    expect(ac.make).toBe("Voltas");
    expect(ac.capacity).toBe("1.5 Ton");
    expect(ac.capacityTon).toBe(1.5);
    expect(ac.status).toBe("Working");
    expect(ac.starRating).toBe("3");
    expect(ac.powerRating).toBe("1.6 kW");
    expect(ac.buildingName).toBe("Academic Block A");
    expect(ac.location).toEqual({ buildingId: "BLK-001", floorId: "F-2", departmentId: "D-CSE", roomId: "R-CSE-L1" });
    expect(ac.acId).toBe(assetRow.ac_id); // raw ac_id kept for write endpoints
    expect("isDemo" in ac).toBe(false); // demo concept removed in Phase 3
  });

  it("carries the full location chain incl. room_type for the detail page", () => {
    const ac = mapAsset({ ...assetRow, room_type: "Workshop" });
    expect(ac.buildingName).toBe("Academic Block A");
    expect(ac.floorName).toBe("Floor 2");
    expect(ac.departmentName).toBe("CSE");
    expect(ac.roomName).toBe("Computer Lab 1");
    expect(ac.roomType).toBe("Workshop"); // Room card renders this first
    expect(ac.location).toEqual({
      buildingId: "BLK-001",
      floorId: "F-2",
      departmentId: "D-CSE",
      roomId: "R-CSE-L1",
    });
  });

  it("leaves roomType undefined when the campus room has no type (no fabrication)", () => {
    expect(mapAsset(assetRow).roomType).toBeUndefined();
  });

  it("carries the centrally derived visual ID and the technical UUID", () => {
    const ac = mapAsset({ ...assetRow, visual_id: "NC-MA-1F-CS-CCL1-0CA6C87C" });
    expect(ac.visualId).toBe("NC-MA-1F-CS-CCL1-0CA6C87C");
    expect(ac.uuid).toBe("123e4567-e89b-12d3-a456-426614174000");
    expect(ac.id).toBe(assetRow.asset_uuid); // routing/QR identity untouched
    // Rows served before visual_id existed map cleanly (callers fall back).
    expect(mapAsset(assetRow).visualId).toBeUndefined();
  });

  it("round-trips the status vocabulary", () => {
    expect(toUiStatus("working")).toBe("Working");
    expect(toUiStatus("not working")).toBe("Fault");
    expect(toUiStatus("under maintenance")).toBe("Maintenance");
    expect(toUiStatus("inactive")).toBe("Offline");
    expect(toUiStatus("FAULT")).toBe("Fault");
    expect(toUiStatus(null)).toBe("Unknown");
    expect(toApiStatus("Fault")).toBe("not working");
    expect(toApiStatus("Maintenance")).toBe("under maintenance");
  });

  it("parses legacy capacity strings", () => {
    expect(parseCapacityTon("1.5 Ton")).toBe(1.5);
    expect(parseCapacityTon("2.0 TON")).toBe(2.0);
    expect(parseCapacityTon("")).toBeUndefined();
  });
});

describe("location payload (source vs confirmed)", () => {
  const unresolvedBlock = { id: null, name: null, status: "UNRESOLVED", source: "Phantom Block" };
  const locationPayload = {
    status: "PARTIAL",
    levels: {
      college: { id: "NITTTR-CHENNAI", name: "NITTTR Chennai", status: "RESOLVED", source: null },
      block: unresolvedBlock,
      floor: { id: null, name: null, status: "MISSING", source: null },
      department: { id: null, name: null, status: "MISSING", source: null },
      room: { id: "ROOM-102", name: "Computer Lab 1", status: "RESOLVED", source: "102", type: "Laboratory" },
    },
    manual_fields: ["room"],
  };

  it("maps the nested payload into locationDetail; legacy rows stay undefined", () => {
    const ac = mapAsset({ ...assetRow, location: locationPayload });
    expect(ac.locationDetail?.status).toBe("PARTIAL");
    expect(ac.locationDetail?.levels.block).toEqual(unresolvedBlock); // source preserved
    expect(ac.locationDetail?.levels.room.type).toBe("Laboratory");
    expect(ac.locationDetail?.manual_fields).toEqual(["room"]);
    // Pre-014 payload without the nested key: no crash, no fabrication.
    expect(mapAsset(assetRow).locationDetail).toBeUndefined();
  });

  it("reads absent levels/statuses as MISSING (defensive, never throws)", () => {
    const detail = toLocationDetail({ status: null, levels: undefined, manual_fields: null });
    expect(detail.status).toBeNull();
    expect(detail.manual_fields).toEqual([]);
    expect(detail.levels.block).toEqual({ id: null, name: null, status: "MISSING", source: null });
    const unknown = toLocationDetail({
      status: "PARTIAL",
      levels: { block: { id: null, name: null, status: "SOMETHING_NEW", source: "X" } },
      manual_fields: [],
    });
    expect(unknown.levels.block.status).toBe("MISSING"); // unknown vocabulary degrades safely
  });

  it("lists only review-needed levels, parent-first (UNRESOLVED/AMBIGUOUS)", () => {
    const detail = mapAsset({ ...assetRow, location: locationPayload }).locationDetail;
    expect(locationReviewLevels(detail)).toEqual(["block"]);
    const both = {
      ...locationPayload,
      levels: {
        ...locationPayload.levels,
        college: { id: null, name: null, status: "AMBIGUOUS", source: "NITTTR" },
        room: { id: null, name: null, status: "UNRESOLVED", source: "GHOST-1" },
      },
    };
    expect(locationReviewLevels(mapAsset({ ...assetRow, location: both }).locationDetail)).toEqual([
      "college",
      "block",
      "room",
    ]);
    expect(locationReviewLevels(undefined)).toEqual([]);
  });

  it("falls back name -> id -> source -> placeholder (effective read rule)", () => {
    expect(locationLevelText({ id: null, name: "Main Block", status: "RESOLVED", source: "Main Block" })).toBe(
      "Main Block",
    );
    expect(locationLevelText({ id: "BLK-1", name: null, status: "RESOLVED", source: null })).toBe("BLK-1");
    expect(locationLevelText({ id: null, name: null, status: "UNRESOLVED", source: "Phantom" })).toBe("Phantom");
    expect(locationLevelText({ id: null, name: null, status: "MISSING", source: null })).toBe("—");
  });

  it("effectiveLocationText unifies every level for ASSIGNED/PARTIAL/PENDING", () => {
    // PARTIAL: confirmed block, source-only room text, resolved department.
    const ac = mapAsset({
      ...assetRow,
      block_id: null,
      block_name: "Phantom Block",
      floor_id: null,
      floor_name: null,
      department_id: "DEPT-ADMINISTRATION",
      department_name: "Administration",
      room_id: null,
      room_name: null,
      room_type: "ADMIN OFFICE",
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
    });
    expect(effectiveLocationText(ac, "block")).toBe("Phantom Block");
    expect(effectiveLocationText(ac, "floor")).toBe("—");
    expect(effectiveLocationText(ac, "department")).toBe("Administration");
    // No physical room: the preserved source room_type wins (never a room).
    expect(effectiveLocationText(ac, "room")).toBe("ADMIN OFFICE");

    // ASSIGNED: resolved room shows type, then name, then id.
    const resolvedPayload = {
      status: "ASSIGNED",
      levels: {
        college: { id: "C-1", name: "College One", status: "RESOLVED", source: null },
        block: { id: "BLK-001", name: "Academic Block A", status: "RESOLVED", source: "Academic Block A" },
        floor: { id: "F-2", name: "Floor 2", status: "RESOLVED", source: "Floor 2" },
        department: { id: "D-CSE", name: "CSE", status: "RESOLVED", source: "CSE" },
        room: {
          id: "R-CSE-L1",
          name: "Computer Lab 1",
          status: "RESOLVED",
          source: "Computer Lab 1",
          type: "Workshop",
        },
      },
      manual_fields: [],
    };
    const assigned = mapAsset({ ...assetRow, room_type: "Workshop", location: resolvedPayload });
    expect(effectiveLocationText(assigned, "block")).toBe("Academic Block A");
    expect(effectiveLocationText(assigned, "floor")).toBe("Floor 2");
    expect(effectiveLocationText(assigned, "department")).toBe("CSE");
    expect(effectiveLocationText(assigned, "room")).toBe("Workshop");

    // PENDING with no nested payload falls back to the flat effective fields.
    const pending = mapAsset({ ...assetRow, location: undefined });
    expect(effectiveLocationText(pending, "block")).toBe("Academic Block A");
    expect(
      effectiveLocationText(
        {
          ...pending,
          buildingName: undefined,
          floorName: undefined,
          roomName: undefined,
          roomType: undefined,
          departmentName: undefined,
          location: { buildingId: "", floorId: "", departmentId: undefined, roomId: "" },
          locationDetail: undefined,
        },
        "block",
      ),
    ).toBe("—");
  });
});

describe("job adapter", () => {
  it("maps SCHEDULED/ASSIGNED to the UI lifecycle", () => {
    expect(toUiJobStatus("SCHEDULED")).toBe("Pending");
    expect(toUiJobStatus("ASSIGNED")).toBe("Accepted");
    expect(toUiJobStatus("IN_PROGRESS")).toBe("In Progress");
    expect(toUiJobStatus("COMPLETED")).toBe("Completed");
  });

  it("builds the location string from joined names", () => {
    const job = mapWorkOrder({
      id: "w1",
      ac_id: "AC-1",
      status: "IN_PROGRESS",
      priority: "HIGH",
      maintenance_type: "Repair",
      block_name: "Block A",
      floor_name: "Floor 1",
      room_name: "Lab 2",
      problem_description: "Not cooling",
    });
    expect(job.status).toBe("In Progress");
    expect(job.priority).toBe("High");
    expect(job.acLocation).toBe("Block A / Floor 1 / Lab 2");
    expect(job.type).toBe("Repair");
  });
});

describe("movement adapter", () => {
  it("maps statuses 1:1 including Cancelled", () => {
    expect(toUiMovementStatus("PENDING")).toBe("Pending");
    expect(toUiMovementStatus("APPROVED")).toBe("Approved");
    expect(toUiMovementStatus("COMPLETED")).toBe("Completed");
    expect(toUiMovementStatus("CANCELLED")).toBe("Cancelled");
    expect(toUiMovementStatus("rejected")).toBe("Rejected");
  });
  it("prefers joined names over raw ids, defaults comments to []", () => {
    const m = mapMovement({
      id: "m1",
      ac_id: "AC-7",
      status: "PENDING",
      to_room_id: "R2",
      to_room_name: "Seminar Hall",
      requested_at: "2026-10-01T10:00:00",
    });
    expect(m.toLocation).toBe("Seminar Hall");
    expect(m.requestDate).toContain("2026-10-01");
    expect(m.comments).toEqual([]);
  });
  it("uses the composed Block / Floor / Room location from the list join", () => {
    const m = mapMovement({
      id: "m2",
      ac_id: "AC-8",
      status: "APPROVED",
      from_location: "Main Academic Block / Floor 1 / ROOM-102",
      to_location: "Main Academic Block / Floor 1 / ROOM-105",
      requested_by_role: "TECHNICIAN",
      comments: [
        { id: "c1", author_name: "Asha", body: "Ready when you are", created_at: "2026-10-02T09:00:00" },
      ],
    });
    expect(m.fromLocation).toBe("Main Academic Block / Floor 1 / ROOM-102");
    expect(m.toLocation).toBe("Main Academic Block / Floor 1 / ROOM-105");
    expect(m.requestedByRole).toBe("TECHNICIAN");
    expect(m.comments).toEqual([
      { id: "c1", author: "Asha", message: "Ready when you are", createdAt: "2026-10-02T09:00:00" },
    ]);
  });
});

describe("user adapter", () => {
  it("maps approval status vocabulary and profile fields", () => {
    expect(toUiUserStatus("PENDING")).toBe("pending");
    const user = mapProfileToUser({
      id: "u-1",
      email: "a@b.io",
      name: "Asha",
      role: "TECHNICIAN",
      status: "APPROVED",
      staff_id: "EMP-9",
      assigned_blocks: ["BLK-001"],
      years_experience: 4,
    });
    expect(user.role).toBe("TECHNICIAN");
    expect(user.status).toBe("approved");
    expect(user.staffId).toBe("EMP-9");
    expect(user.assignedBlocks).toEqual(["BLK-001"]);
  });

  it("PATCH payload never includes role or status", () => {
    const payload = toProfilePatch({ name: "X", phone: "1", role: "SUPER_ADMIN" as never, status: "approved" });
    expect(payload).toEqual({ name: "X", phone: "1" });
    expect("role" in payload).toBe(false);
    expect("status" in payload).toBe(false);
  });
});
