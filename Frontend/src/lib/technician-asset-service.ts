/**
 * Technician asset lookups (Phase 2): backed by GET /api/v1/assets and
 * GET /api/v1/assets/<ref> through the shared API client + asset adapter.
 * The local cache (src/lib/storage) is an offline read-through cache only —
 * the PostgreSQL backend remains the source of truth (D7).
 *
 * Direct access to Next route handlers (/api/assets*) was removed in Phase 2
 * task 1; those handlers spoke to Postgres with `pg`, which no longer exists.
 */
import { api, apiList, ApiError } from "@/lib/api/client";
import { mapAsset, type ApiAsset } from "@/lib/adapters/asset";
import type { ExtendedAC } from "@/lib/types/asset";
import { readCache, writeCache } from "@/lib/storage";

export interface TechnicianAsset {
  id: string;
  /** Business key (AC-…) for work-order filters/create — route ids may be UUIDs. */
  acId?: string;
  location: { block: string; blockType: string; department: string | null; floor: string; room: string; roomType: string };
  details: { make: string; model: string; serialNumber: string; capacity: string; capacityTon: number | null; type: string; manufacturingYear: number | null; installationYear: number | null; installationDate: string | null };
  technical: { status: string; starRating: string | null; powerRating: string | null; powerFactor: number | null; energyMeterReading: number | null };
  maintenance: { lastType: string | null; lastDate: string | null; nextDate: string | null; remarks: string | null };
  maintenanceHistory?: { id: string; type: string; status: string; priority: string; description: string; scheduledDate: string; completedDate: string | null; notes: string | null; assignedTo: string | null }[];
  movementHistory?: { id: string; fromLocation: string | null; toLocation: string; status: string; reason: string; requestDate: string }[];
}

const ASSET_CACHE_KEY = "technician_asset_cache";

function toTechnicianAsset(ac: ExtendedAC): TechnicianAsset {
  return {
    id: ac.id,
    acId: ac.acId,
    location: {
      block: ac.buildingName ?? ac.location.buildingId ?? "",
      blockType: "",
      department: ac.departmentName ?? null,
      floor: ac.floorName ?? "",
      room: ac.roomName ?? "",
      roomType: "",
    },
    details: {
      make: ac.make,
      model: ac.model,
      serialNumber: ac.serialNumber,
      capacity: ac.capacity,
      capacityTon: ac.capacityTon ?? null,
      type: ac.type,
      manufacturingYear: ac.manufacturingYear ? Number.parseInt(ac.manufacturingYear, 10) : null,
      installationYear: ac.installationYear ? Number.parseInt(ac.installationYear, 10) : null,
      installationDate: ac.installationDate ?? null,
    },
    technical: {
      status: ac.status,
      starRating: ac.starRating ?? null,
      powerRating: ac.powerRating ?? null,
      powerFactor: ac.powerFactor ? Number.parseFloat(ac.powerFactor) : null,
      energyMeterReading: null,
    },
    maintenance: {
      lastType: ac.lastMaintenanceType ?? null,
      lastDate: ac.lastMaintenanceDate ?? null,
      nextDate: ac.nextMaintenanceDate ?? null,
      remarks: ac.remarks ?? null,
    },
  };
}

function readCached(): TechnicianAsset[] {
  return readCache<TechnicianAsset[]>(ASSET_CACHE_KEY, []);
}

function writeCached(assets: TechnicianAsset[]): void {
  writeCache(ASSET_CACHE_KEY, assets);
}

export function getCachedAssets(): TechnicianAsset[] {
  return readCached();
}

export function getCachedAsset(id: string): TechnicianAsset | undefined {
  const needle = id.trim().toLowerCase();
  return readCached().find(
    (asset) =>
      asset.id.toLowerCase() === needle ||
      asset.details.serialNumber.toLowerCase() === needle,
  );
}

export async function searchTechnicianAssets(search = ""): Promise<TechnicianAsset[]> {
  const query = search.trim().toLowerCase();
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    return readCached().filter(
      (asset) =>
        !query ||
        asset.id.toLowerCase().includes(query) ||
        asset.details.serialNumber.toLowerCase().includes(query),
    );
  }
  const params = search.trim() ? `?search=${encodeURIComponent(search.trim())}&page_size=50` : "?page_size=50";
  const page = await apiList<ApiAsset>(`/api/v1/assets${params}`);
  const assets = page.items.map(mapAsset).map(toTechnicianAsset);
  // Merge: fresh API rows win; keep cached rows not present in this page.
  const merged = [
    ...readCached().filter((cached) => !assets.some((a) => a.id === cached.id)),
    ...assets,
  ];
  writeCached(merged);
  return assets;
}

export async function getTechnicianAsset(id: string): Promise<TechnicianAsset | null> {
  const cached = getCachedAsset(id);
  if (typeof navigator !== "undefined" && !navigator.onLine) return cached ?? null;
  try {
    const row = await api<ApiAsset>(`/api/v1/assets/${encodeURIComponent(id)}`);
    const asset = toTechnicianAsset(mapAsset(row));
    writeCached([...readCached().filter((item) => item.id !== asset.id), asset]);
    return asset;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return cached ?? null;
    return cached ?? null;
  }
}
