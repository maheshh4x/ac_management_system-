/**
 * Asset domain types (Phase 3) — home of `ExtendedAC` & friends after the
 * localStorage store (`ac-data-service.ts`) was retired. Types only: no
 * store, no side effects; data comes from `src/lib/api/assets.ts` hooks.
 */
import type { AcStatus, LocationData } from "./campus";

export interface ExtendedAC {
  id: string; // AC ID (asset_uuid when known, else ac_id) — stable URL/QR key
  acId?: string; // raw backend ac_id (when id is the uuid)
  /** Technical UUID (QR payload, routing). Display visualId instead. */
  uuid?: string;
  /** Centrally derived human-readable identifier (backend visual_id.py). */
  visualId?: string;
  make: string; // Brand
  model: string;
  capacity: string; // e.g., "1.5 Ton"
  capacityTon?: number; // e.g., 1.5
  serialNumber: string;
  type: string; // e.g., "Split", "Window", "Cassette", "Central", "Ductable"
  manufacturingYear?: string;
  installationYear?: string;
  installationDate?: string;
  starRating?: string;
  powerRating?: string;
  powerFactor?: string;
  status: AcStatus; // 'Working' | 'Fault' | 'Maintenance' | 'Offline' | 'Unknown'
  operationalStatus?: string; // constrained vocabulary: WORKING|FAULT|...
  lastMaintenanceType?: string;
  lastMaintenanceDate?: string;
  nextMaintenanceDate?: string;
  technician?: string;
  remarks?: string;
  location: LocationData;
  /**
   * Nested source-vs-confirmed payload from the location model (§7): per
   * field id/name/status/source + manual pins. Absent on rows served before
   * migration 014; the flat `location`/`buildingName`/`roomName` fields above
   * stay the display fallback.
   */
  locationDetail?: AssetLocationDetail;
  buildingName?: string;
  floorName?: string;
  roomName?: string;
  /** `rooms.room_type` (canonical import's room type); room display prefers it. */
  roomType?: string;
  departmentName?: string;
  lifecycleStatus?: string;
  isActive?: boolean;
  /** Indoor/outdoor unit designation (migration 020); defaults to INDOOR. */
  unitRole?: string;
  /** ac_id of the linked indoor parent (OUTDOOR rows only). */
  parentAcId?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface ACFilters {
  search?: string;
  buildingId?: string;
  floorId?: string;
  departmentId?: string;
  collegeId?: string;
  manufacturer?: string;
  capacityTon?: string;
  acType?: string;
  status?: string;
  yearFrom?: string;
  yearTo?: string;
  dateRange?: { start?: string; end?: string };
}

export interface ACStatistics {
  total: number;
  working: number;
  faulty: number;
  underMaintenance: number;
  inactive: number;
  dueForMaintenance: number;
  overdue: number;
  installedCapacity: number;
  departmentsCount: number;
  locationsCount: number;
  dataCompletenessPct: number;
}

/** One bucket of a `GET /api/v1/assets/facets` grouping. */
export interface FacetEntry {
  value: string;
  count: number;
  /** Stable canonical id on block/department facets (null = source-only
      value with no canonical entity); absent on the other facets. */
  id?: string | null;
}

export interface AssetFacets {
  status: FacetEntry[];
  asset_type: FacetEntry[];
  manufacturer: FacetEntry[];
  college: FacetEntry[];
  block: FacetEntry[];
  floor: FacetEntry[];
  department: FacetEntry[];
  lifecycle: FacetEntry[];
  location_status: FacetEntry[];
}

/** Chart/feed shapes (name/value pairs) built from the dashboard summary. */
export interface DistributionEntry {
  name: string;
  value: number;
  color?: string;
}

/** Draft produced by ACTable's inline editor (UI vocabulary). */
export interface InlineDraft {
  status?: ExtendedAC['status'];
  technician?: string;
  remarks?: string;
  lastMaintenanceDate?: string;
  nextMaintenanceDate?: string;
}

// ── Permanent location model (source vs confirmed) ───────────────────────────

/** Hierarchy levels resolved per field by the backend resolver. */
export type LocationLevelKey = 'college' | 'block' | 'floor' | 'department' | 'room';

/** Per-field resolution state — RESOLVED/UNRESOLVED/MISSING/AMBIGUOUS. */
export type LocationLevelStatus = 'RESOLVED' | 'UNRESOLVED' | 'MISSING' | 'AMBIGUOUS';

export interface AssetLocationLevel {
  /** Confirmed canonical id — null while the level still needs review. */
  id: string | null;
  /** Canonical master name (null until the id resolves). */
  name: string | null;
  status: LocationLevelStatus;
  /** Text the import supplied — preserved verbatim, never discarded. */
  source: string | null;
  /** Room level only: `rooms.room_type` from the canonical import. */
  type?: string | null;
}

export interface AssetLocationDetail {
  /** Overall: ASSIGNED | PARTIAL | PENDING (the extended acs vocabulary). */
  status: string | null;
  levels: Record<LocationLevelKey, AssetLocationLevel>;
  /** Levels manually confirmed — imports and re-resolves never change them. */
  manual_fields: string[];
}
