/**
 * Asset adapter (Phase 2, task 7) — the ONLY place that translates
 * `/api/v1/assets` rows into the frontend `ExtendedAC` domain type.
 * Implements the vocabulary table from 01-assessment-and-decisions §4:
 * status words, capacity strings, star/power numerics, uuid/code identity.
 * Responses are zod-validated at this boundary; pages never see raw rows.
 */
import { z } from "zod";
import type { AcStatus } from "@/lib/types/campus";
import type { AssetLocationDetail, AssetLocationLevel, ExtendedAC, InlineDraft, LocationLevelKey, LocationLevelStatus } from "@/lib/types/asset";

// ── Nested location payload (permanent location model, §7) ──────────────────
// Declared before apiAssetSchema (which embeds it): const initializers run
// top-to-bottom at module load, so this block must come first.

const apiLocationLevelSchema = z
  .object({
    id: z.string().nullish(),
    name: z.string().nullish(),
    status: z.string().nullish(),
    source: z.string().nullish(),
    type: z.string().nullish(),
  })
  .loose();

const apiLocationSchema = z
  .object({
    status: z.string().nullish(),
    levels: z
      .object({
        college: apiLocationLevelSchema.nullish(),
        block: apiLocationLevelSchema.nullish(),
        floor: apiLocationLevelSchema.nullish(),
        department: apiLocationLevelSchema.nullish(),
        room: apiLocationLevelSchema.nullish(),
      })
      .nullish(),
    manual_fields: z.array(z.string()).nullish(),
  })
  .loose();

export type ApiLocation = z.infer<typeof apiLocationSchema>;

/** Every hierarchy level, in parent-first display order. */
export const LOCATION_LEVEL_KEYS = ["college", "block", "floor", "department", "room"] as const satisfies readonly LocationLevelKey[];

/** Loose-but-safe: unknown extra keys pass through; required shape enforced. */
export const apiAssetSchema = z
  .object({
    ac_id: z.string(),
    asset_uuid: z.string().nullish(),
    asset_code: z.string().nullish(),
    room_id: z.string().nullish(),
    room_name: z.string().nullish(),
    room_type: z.string().nullish(),
    floor_id: z.string().nullish(),
    floor_name: z.string().nullish(),
    block_id: z.string().nullish(),
    block_name: z.string().nullish(),
    department_id: z.string().nullish(),
    department_name: z.string().nullish(),
    ac_make_company: z.string().nullish(),
    ac_model_number: z.string().nullish(),
    ac_serial_number: z.string().nullish(),
    ac_capacity_ton: z.coerce.number().nullish(),
    ac_type: z.string().nullish(),
    manufacturing_year: z.coerce.number().nullish(),
    installation_year: z.coerce.number().nullish(),
    current_status: z.string().nullish(),
    operational_status: z.string().nullish(),
    location_status: z.string().nullish(),
    lifecycle_status: z.string().nullish(),
    is_active: z.boolean().nullish(),
    ac_star_rating: z.coerce.number().nullish(),
    power_rating_kw: z.coerce.number().nullish(),
    power_factor: z.coerce.number().nullish(),
    installation_date: z.string().nullish(),
    last_maintenance_date: z.string().nullish(),
    next_service_date: z.string().nullish(),
    technician: z.string().nullish(),
    remarks: z.string().nullish(),
    created_at: z.string().nullish(),
    updated_at: z.string().nullish(),
    location: apiLocationSchema.nullish(),
    // Centrally derived display identifier (backend visual_id.py); rows
    // served before it existed simply map to undefined (callers fall back).
    visual_id: z.string().nullish(),
  })
  .loose();

export type ApiAsset = z.infer<typeof apiAssetSchema>;

/** Backend free-text/constrained status -> UI vocabulary. */
export function toUiStatus(status: string | null | undefined): AcStatus {
  switch ((status ?? "").trim().toLowerCase()) {
    case "working":
      return "Working";
    case "not working":
    case "damaged":
    case "fault":
      return "Fault";
    case "under maintenance":
    case "maintenance":
      return "Maintenance";
    case "inactive":
    case "offline":
      return "Offline";
    default:
      return "Unknown";
  }
}

/** Phase 3: prefer the constrained operational_status when present. */
function toUiStatusFromRow(operational: string | null | undefined, legacy: string | null | undefined): AcStatus {
  const key = (operational ?? "").trim().toUpperCase();
  if (key === "WORKING") return "Working";
  if (key === "FAULT") return "Fault";
  if (key === "MAINTENANCE") return "Maintenance";
  if (key === "OFFLINE") return "Offline";
  if (key === "UNKNOWN") return "Unknown";
  return toUiStatus(legacy);
}

/** UI vocabulary -> backend write value. */
export function toApiStatus(status: AcStatus): string {
  switch (status) {
    case "Working":
      return "working";
    case "Fault":
      return "not working";
    case "Maintenance":
      return "under maintenance";
    case "Offline":
      return "inactive";
    default:
      return "unknown";
  }
}

/** Legacy "1.5 Ton" string <-> numeric capacity. */
export function parseCapacityTon(capacity: string): number | undefined {
  const match = capacity.match(/([0-9]+(?:\.[0-9]+)?)/);
  return match ? Number.parseFloat(match[1]) : undefined;
}

export function mapAsset(row: ApiAsset): ExtendedAC {
  const a = apiAssetSchema.parse(row);
  const ton = a.ac_capacity_ton ?? undefined;
  return {
    id: a.asset_uuid ?? a.ac_id, // stable URL/QR key per vocabulary table
    acId: a.ac_id,
    uuid: a.asset_uuid ?? undefined, // technical identity (QR payload, routing)
    // Human-readable identifier, derived server-side from the effective
    // location + uuid suffix (visual_id.py). Display only — never identity.
    visualId: a.visual_id ?? undefined,
    make: a.ac_make_company ?? "",
    model: a.ac_model_number ?? "",
    capacity: ton !== undefined ? `${ton} Ton` : "",
    capacityTon: ton,
    serialNumber: a.ac_serial_number ?? "",
    type: a.ac_type ?? "",
    manufacturingYear: a.manufacturing_year != null ? String(a.manufacturing_year) : undefined,
    installationYear: a.installation_year != null ? String(a.installation_year) : undefined,
    installationDate: a.installation_date ?? undefined,
    starRating: a.ac_star_rating != null ? String(a.ac_star_rating) : undefined,
    powerRating: a.power_rating_kw != null ? `${a.power_rating_kw} kW` : undefined,
    powerFactor: a.power_factor != null ? String(a.power_factor) : undefined,
    status: toUiStatusFromRow(a.operational_status, a.current_status),
    operationalStatus: a.operational_status ?? undefined,
    lastMaintenanceDate: a.last_maintenance_date ?? undefined,
    nextMaintenanceDate: a.next_service_date ?? undefined,
    technician: a.technician ?? undefined,
    remarks: a.remarks ?? undefined,
    location: {
      buildingId: a.block_id ?? "",
      floorId: a.floor_id ?? "",
      departmentId: a.department_id ?? undefined,
      roomId: a.room_id ?? "",
    },
    buildingName: a.block_name ?? undefined,
    floorName: a.floor_name ?? undefined,
    roomName: a.room_name ?? undefined,
    // room_type was parsed by the schema but dropped here, so the detail
    // page could never show the intended "Room = room type" value.
    roomType: a.room_type ?? undefined,
    departmentName: a.department_name ?? undefined,
    locationDetail: a.location ? toLocationDetail(a.location) : undefined,
    lifecycleStatus: a.lifecycle_status ?? undefined,
    isActive: a.is_active ?? undefined,
    createdAt: a.created_at ?? undefined,
    updatedAt: a.updated_at ?? undefined,
  };
}

// ── Location model helpers (source vs confirmed) ────────────────────────────

/** Defensive: an unknown status (old payload, future vocabulary) reads as MISSING. */
function toLevelStatus(raw: string | null | undefined): LocationLevelStatus {
  return raw === "RESOLVED" || raw === "UNRESOLVED" || raw === "AMBIGUOUS" ? raw : "MISSING";
}

/** Normalize the nested payload into the domain type; never throws. */
export function toLocationDetail(raw: ApiLocation): AssetLocationDetail {
  const levels = {} as Record<LocationLevelKey, AssetLocationLevel>;
  for (const key of LOCATION_LEVEL_KEYS) {
    const level = raw.levels?.[key];
    levels[key] = {
      id: level?.id ?? null,
      name: level?.name ?? null,
      status: toLevelStatus(level?.status),
      source: level?.source ?? null,
      ...(key === "room" ? { type: level?.type ?? null } : {}),
    };
  }
  return { status: raw.status ?? null, levels, manual_fields: raw.manual_fields ?? [] };
}

/**
 * Levels whose preserved source text has no confirmed id — the §13 review
 * queue. By the model's invariant, UNRESOLVED/AMBIGUOUS mean "source present,
 * id NULL"; MISSING is nothing to review and RESOLVED is settled.
 */
export function locationReviewLevels(detail?: AssetLocationDetail | null): LocationLevelKey[] {
  if (!detail?.levels) return [];
  return LOCATION_LEVEL_KEYS.filter((key) => {
    const status = detail.levels[key]?.status;
    return status === "UNRESOLVED" || status === "AMBIGUOUS";
  });
}

/**
 * Effective display text — mirrors the backend's read rule:
 * canonical name -> canonical id -> preserved source text -> placeholder.
 */
export function locationLevelText(level?: AssetLocationLevel | null): string {
  return level?.name || level?.id || level?.source || "—";
}

/**
 * ONE effective-location helper for the whole detail UI (§7): canonical
 * name -> canonical id -> preserved source value -> "—", for ASSIGNED,
 * PARTIAL and PENDING alike, without fabricating data.
 *
 * Reads the nested locationDetail first (it keeps the canonical/source
 * split); falls back to the flat fields — which already carry the
 * backend's effective text — when no nested payload exists.
 *
 * Room is descriptive-first: a resolved room shows its type, then name,
 * then id (the intended mapping); with no physical room the preserved
 * source room_type wins over the source room number, because a type is
 * metadata and must never fabricate a room.
 */
export function effectiveLocationText(ac: ExtendedAC, level: LocationLevelKey): string {
  const lvl = ac.locationDetail?.levels?.[level];
  if (level === "room") {
    if (lvl?.id) return ac.roomType || lvl.type || lvl.name || lvl.id;
    return ac.roomType || lvl?.source || ac.roomName || ac.location?.roomId || "—";
  }
  if (level === "college") return lvl?.name || lvl?.id || lvl?.source || "—";
  const flatName =
    level === "block" ? ac.buildingName : level === "floor" ? ac.floorName : ac.departmentName;
  const flatId =
    level === "block"
      ? ac.location?.buildingId
      : level === "floor"
        ? ac.location?.floorId
        : ac.location?.departmentId;
  return lvl?.name || lvl?.id || lvl?.source || flatName || flatId || "—";
}

/** Frontend -> API create/update payload (widening lands fully in P3). */
export function toAssetPayload(ac: Partial<ExtendedAC>): Record<string, unknown> {  const payload: Record<string, unknown> = {};
  if (ac.id) payload.ac_id = ac.id;
  if (ac.location?.roomId) payload.room_id = ac.location.roomId;
  if (ac.make !== undefined) payload.ac_make_company = ac.make || null;
  if (ac.model !== undefined) payload.ac_model_number = ac.model || null;
  if (ac.serialNumber !== undefined) payload.ac_serial_number = ac.serialNumber || null;
  if (ac.type !== undefined) payload.ac_type = ac.type || null;
  if (ac.capacityTon ?? parseCapacityTon(ac.capacity ?? "")) {
    payload.ac_capacity_ton = ac.capacityTon ?? parseCapacityTon(ac.capacity ?? "");
  }
  if (ac.status !== undefined) payload.current_status = toApiStatus(ac.status);
  if (ac.installationDate !== undefined) payload.installation_date = ac.installationDate || null;
  if (ac.remarks !== undefined) payload.remarks = ac.remarks || null;
  return payload;
}

/**
 * ACTable's inline editor draft -> PATCH payload. Empty strings become null
 * so the server clears those columns (Phase 3 PATCH honours explicit nulls).
 */
export function inlineDraftToPayload(draft: InlineDraft): Record<string, unknown> {
  const blank = (v: string | undefined) => (v && v.trim() !== '' ? v.trim() : null);
  return {
    current_status: toApiStatus(draft.status ?? 'Unknown'),
    technician: blank(draft.technician),
    remarks: blank(draft.remarks),
    last_maintenance_date: blank(draft.lastMaintenanceDate),
    next_service_date: blank(draft.nextMaintenanceDate),
  };
}

/**
 * ACDetailView's inline edit draft -> PATCH payload. Only keys present in the
 * draft are sent; empty strings become null so the server clears the column.
 */
export function detailEditToPayload(edited: Partial<ExtendedAC>): Record<string, unknown> {
  const blank = (v: string | undefined) => (v && v.trim() !== '' ? v.trim() : null);
  const payload: Record<string, unknown> = {};
  if (edited.make !== undefined) payload.ac_make_company = blank(edited.make);
  if (edited.model !== undefined) payload.ac_model_number = blank(edited.model);
  if (edited.serialNumber !== undefined) payload.ac_serial_number = blank(edited.serialNumber);
  if (edited.status !== undefined) payload.current_status = toApiStatus(edited.status);
  if (edited.technician !== undefined) payload.technician = blank(edited.technician);
  if (edited.remarks !== undefined) payload.remarks = blank(edited.remarks);
  return payload;
}
