// src/lib/campus-services.ts
/**
 * Phase 3 task 5 — campus reads are served by /api/v1/campus/tree and the
 * asset endpoints. These async facades keep the historical signatures that
 * CampusLayout / FloorView / CampusOverlay / GlobalSearch / DashboardPreview
 * already call; writes live in CampusManagementPage via the CRUD endpoints
 * (react-query mutations in lib/api/campus.ts).
 */
import { Building, Department, Floor, Room, AC } from './types/campus';
import { api, type Paginated } from './api/client';
import { mapAsset, type ApiAsset } from './adapters/asset';
import type { DashboardSummary } from './api/assets';
import type { CampusBlock, CampusFloor, CampusRoom, CampusTree } from './api/campus';

// ── tree cache for non-hook consumers (short TTL, in-flight dedup) ──────────
let treeCache: { at: number; tree: CampusTree } | null = null;
let inflight: Promise<CampusTree> | null = null;
const TREE_TTL_MS = 30_000;

export async function fetchCampusTree(force = false): Promise<CampusTree> {
  if (!force && treeCache && Date.now() - treeCache.at < TREE_TTL_MS) return treeCache.tree;
  if (!inflight) {
    inflight = api<CampusTree>('/api/v1/campus/tree')
      .then(tree => {
        treeCache = { at: Date.now(), tree };
        return tree;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Call after a campus write so the next facade read reflects it. */
export function invalidateCampusTreeCache() {
  treeCache = null;
}

// ── tree -> UI mappers (shared with CampusManagementPage) ──────────────────

export function allBlocks(tree: CampusTree): CampusBlock[] {
  return [...(tree.colleges ?? []).flatMap(college => college.blocks ?? []), ...(tree.orphan_blocks ?? [])];
}

export function toBuilding(block: CampusBlock): Building {
  return {
    id: block.block_id,
    name: block.block_name,
    type: '',
    status: 'Active',
    photos: (block.photos ?? []).map(photo => ({
      id: photo.id,
      name: photo.name ?? photo.id,
      // Signed READ URL (1 h) — the DB stores object paths, never blobs.
      dataUrl: photo.signed_url ?? '',
      isCover: Boolean(photo.is_cover),
    })),
    layout: (block.layout ?? undefined) as Building['layout'] | undefined,
  };
}

export function toFloors(block: CampusBlock): Floor[] {
  return (block.floors ?? []).map((floor, index) => ({
    id: floor.floor_id,
    name: floor.floor_name,
    buildingId: floor.block_id || block.block_id,
    order: floor.floor_number ?? index,
  }));
}

export function toRoom(room: CampusRoom): Room {
  return {
    id: room.room_id,
    name: room.room_name,
    type: room.room_type ?? '',
    floorId: room.floor_id,
  };
}

function flattenFloors(tree: CampusTree): { floor: CampusFloor; block: CampusBlock }[] {
  return allBlocks(tree).flatMap(block => (block.floors ?? []).map(floor => ({ floor, block })));
}

function flattenRooms(tree: CampusTree): Room[] {
  return flattenFloors(tree).flatMap(({ floor }) => (floor.rooms ?? []).map(toRoom));
}

// ── historical async API (same signatures as before) ───────────────────────

export const getBuildings = async (): Promise<Building[]> => {
  const tree = await fetchCampusTree();
  return allBlocks(tree).map(toBuilding);
};

export const getBuilding = async (id: string): Promise<Building | undefined> => {
  return (await getBuildings()).find(b => b.id === id);
};

export const getFloors = async (buildingId: string): Promise<Floor[]> => {
  const tree = await fetchCampusTree();
  const block = allBlocks(tree).find(b => b.block_id === buildingId);
  return block ? toFloors(block) : [];
};

export const getFloor = async (id: string): Promise<Floor | undefined> => {
  const tree = await fetchCampusTree();
  for (const block of allBlocks(tree)) {
    const match = toFloors(block).find(f => f.id === id);
    if (match) return match;
  }
  return undefined;
};

export const getDepartments = async (_floorId: string): Promise<Department[]> => {
  // Departments are college-wide on the server (rooms reference them), so the
  // floor-scoped signature is kept for callers but no longer filters.
  const tree = await fetchCampusTree();
  return (tree.departments ?? []).map(department => ({
    id: department.department_id,
    name: department.department_name,
    floorId: '',
  }));
};

export const getRooms = async (floorId: string): Promise<Room[]> => {
  const tree = await fetchCampusTree();
  if (!floorId) return flattenRooms(tree);
  return flattenFloors(tree)
    .filter(({ floor }) => floor.floor_id === floorId)
    .flatMap(({ floor }) => (floor.rooms ?? []).map(toRoom));
};

export const getACs = async (roomId: string): Promise<AC[]> => {
  if (!roomId) return [];
  const page = await api<Paginated<ApiAsset>>(
    `/api/v1/assets?room=${encodeURIComponent(roomId)}&page_size=100`,
  );
  return page.items.map(mapAsset);
};

export const getAC = async (id: string): Promise<AC | undefined> => {
  const row = await api<ApiAsset>(`/api/v1/assets/${encodeURIComponent(id)}`);
  return mapAsset(row);
};

export const searchAssets = async (query: string): Promise<{
  acs: AC[];
  buildings: Building[];
  rooms: Room[];
}> => {
  const lowerQuery = query.toLowerCase().trim();
  if (!lowerQuery) return { acs: [], buildings: [], rooms: [] };

  const [assetPage, buildings, rooms] = await Promise.all([
    api<Paginated<ApiAsset>>(
      `/api/v1/assets?search=${encodeURIComponent(lowerQuery)}&page_size=20`,
    ),
    getBuildings(),
    getRooms(''),
  ]);

  return {
    acs: assetPage.items.map(mapAsset),
    buildings: buildings.filter(b => `${b.name} ${b.id}`.toLowerCase().includes(lowerQuery)),
    rooms: rooms.filter(r => `${r.name} ${r.id}`.toLowerCase().includes(lowerQuery)),
  };
};

export const getCampusStats = async () => {
  const [summary, tree] = await Promise.all([
    api<DashboardSummary>('/api/v1/dashboard/summary'),
    fetchCampusTree(),
  ]);
  const k = summary.kpis;
  return {
    totalACs: k.total,
    working: k.working,
    faulty: k.faulty,
    maintenance: k.under_maint,
    buildingsCount: allBlocks(tree).length,
  };
};
