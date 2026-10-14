'use client';

import React, { ChangeEvent, DragEvent, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { useQueryClient } from '@tanstack/react-query';
import { useCampusTree, type CampusPhoto } from '@/lib/api/campus';
import { api, apiFormData, apiList } from '@/lib/api/client';
import { type ApiAsset } from '@/lib/adapters/asset';
import {
  allBlocks, fetchCampusTree, invalidateCampusTreeCache, toBuilding, toFloors, toRoom,
} from '@/lib/campus-services';
import { useAuth } from '@/contexts/AuthContext';
import { hasPermission } from '@/lib/permissions';
import LocationReviewPanel from '@/components/campus/LocationReviewPanel';
import { Building, BuildingPhoto, Floor, Room } from '@/lib/types/campus';
import {
  Building2, Eye, Grid3X3, ImagePlus, Layers, Pencil,
  Plus, Trash2, Upload, X, MoreVertical, ArrowUp, ArrowDown,
} from 'lucide-react';

type CampusTab = 'buildings' | 'departments' | 'rooms' | 'location';
type EditorMode = 'view' | 'edit';

/** Draft of the block editor — only fields the server columns support. */
interface BlockDraft {
  id: string;
  name: string;
  photos: BuildingPhoto[];
}

interface FormErrors {
  name?: string;
  id?: string;
  floors?: string;
}

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function sortFloors(floors: Floor[]) {
  return [...floors].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

const enc = encodeURIComponent;

export default function CampusManagementPage() {
  const { user } = useAuth();
  // §13 review queue needs MANAGE_CAMPUS (admin/manager) — same gate the
  // backend enforces, so the tab never shows a wall of 403s.
  const canManageCampus = hasPermission(user?.role ?? null, 'MANAGE_CAMPUS');
  const isViewer = user?.role === 'VIEWER';
  const qc = useQueryClient();

  // Single source of truth: GET /api/v1/campus/tree (Phase 3 task 5).
  const { data: tree, isLoading } = useCampusTree();

  const data = useMemo(() => {
    const blocks = allBlocks(tree ?? { colleges: [], orphan_blocks: [], departments: [] });
    return {
      buildings: blocks.map(toBuilding),
      floors: blocks.flatMap(toFloors),
      rooms: blocks.flatMap(block =>
        (block.floors ?? []).flatMap(floor => (floor.rooms ?? []).map(toRoom)),
      ),
      departments: (tree?.departments ?? []).map(department => ({
        id: department.department_id,
        name: department.department_name,
        floorId: '',
      })),
    };
  }, [tree]);

  const [activeTab, setActiveTab] = useState<CampusTab>('buildings');
  const [editorMode, setEditorMode] = useState<EditorMode>('edit');
  const [draft, setDraft] = useState<BlockDraft | null>(null);
  const [editingBuildingId, setEditingBuildingId] = useState<string | null>(null);
  const [draftFloors, setDraftFloors] = useState<Floor[]>([]);
  const [draftRooms, setDraftRooms] = useState<Room[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [errors, setErrors] = useState<FormErrors>({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Building | null>(null);
  const [lightbox, setLightbox] = useState<BuildingPhoto | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState('');

  const notify = (type: 'success' | 'error', message: string) => {
    setToast({ type, message });
    window.setTimeout(() => setToast(null), 4000);
  };

  /** Refresh react-query caches + the facade's module cache after a write. */
  const refreshAll = async () => {
    invalidateCampusTreeCache();
    void qc.invalidateQueries({ queryKey: ['campus'] });
    // Asset rows embed the location chain.
    void qc.invalidateQueries({ queryKey: ['assets'] });
    await fetchCampusTree(true).catch(() => undefined);
  };

  const openEditor = (building: Building | null, mode: EditorMode) => {
    if (isViewer && mode !== 'view') return;
    const id = building?.id || '';
    const floors = building
      ? sortFloors(data.floors.filter(floor => floor.buildingId === id))
      : [{ id: makeId('floor'), name: 'Ground Floor', buildingId: '', order: 0 }];
    setDraft(building ? { id: building.id, name: building.name, photos: building.photos || [] } : { id: '', name: '', photos: [] });
    setEditingBuildingId(building?.id || null);
    setDraftFloors(floors);
    setDraftRooms(building ? data.rooms.filter(room => floors.some(floor => floor.id === room.floorId)) : []);
    setErrors({});
    setEditorMode(mode);
    setShowAdd(!building);
    setEditorOpen(true);
  };

  const closeEditor = () => {
    if (!saving) {
      setEditorOpen(false);
      setDraft(null);
      setEditingBuildingId(null);
      setLightbox(null);
    }
  };

  const moveFloor = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draftFloors.length) return;
    const next = [...draftFloors];
    [next[index], next[target]] = [next[target], next[index]];
    setDraftFloors(next.map((floor, order) => ({ ...floor, order })));
  };

  const addFloor = () => {
    setDraftFloors(current => [...current, { id: makeId('floor'), name: `Floor ${current.length}`, buildingId: draft?.id || '', order: current.length }]);
  };

  const deleteFloor = (floorId: string) => {
    const floor = draftFloors.find(item => item.id === floorId);
    if (!floor || !window.confirm(`Delete ${floor.name} and its ${draftRooms.filter(room => room.floorId === floorId).length} room(s)?`)) return;
    // Draft-only: the save flow diffs against the server; AC-linked rooms are
    // rejected up-front by the pre-flight check (the server enforces it too).
    setDraftFloors(current => current.filter(item => item.id !== floorId).map((item, order) => ({ ...item, order })));
    setDraftRooms(current => current.filter(room => room.floorId !== floorId));
  };

  const addRoom = (floorId: string) => {
    setDraftRooms(current => [...current, { id: makeId('room'), name: 'New Room', type: 'Classroom', floorId, order: current.filter(room => room.floorId === floorId).length }]);
  };

  /** Assets pointing at any of `roomIds` within the block. */
  const linkedRoomIds = async (blockName: string): Promise<Set<string>> => {
    // apiList matches the paginated envelope (`data` = array, `pagination`
    // sibling); `api<Paginated<>>` returned the bare array and crashed on
    // `.items` (same contract defect as campus-services searchAssets).
    const page = await apiList<ApiAsset>(
      `/api/v1/assets?block=${enc(blockName)}&page_size=500`,
    );
    return new Set(page.items.map(item => item.room_id).filter(Boolean) as string[]);
  };

  /**
   * Persist the block + its floor/room diff through the campus CRUD API.
   * Order matters: create new floors first (rooms need their ids), delete
   * removed rooms before removed floors (server HAS_CHILDREN guards).
   */
  const saveBlock = async () => {
    if (!draft) return;
    const nextErrors: FormErrors = {};
    const name = draft.name.trim();
    const id = draft.id.trim().toUpperCase();
    if (!name) nextErrors.name = 'Block name is required.';
    if (!id) nextErrors.id = 'Block code is required.';
    if (showAdd && data.buildings.some(building => building.id === id)) nextErrors.id = 'Block code must be unique.';
    if (draftFloors.length < 1) nextErrors.floors = 'At least one floor is required.';
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      return;
    }

    setSaving(true);
    try {
      const blockId = showAdd ? id : editingBuildingId;
      if (!blockId) throw new Error('Block not found.');
      const originalBlock = data.buildings.find(building => building.id === blockId);

      if (showAdd) {
        await api('/api/v1/campus/blocks', {
          method: 'POST',
          body: { block_id: id, block_name: name },
        });
      } else if (originalBlock && originalBlock.name !== name) {
        await api(`/api/v1/campus/blocks/${enc(blockId)}`, {
          method: 'PATCH',
          body: { block_name: name },
        });
      }

      const originalFloors = showAdd ? [] : data.floors.filter(floor => floor.buildingId === blockId);
      const originalFloorIds = new Set(originalFloors.map(floor => floor.id));
      const originalRooms = showAdd ? [] : data.rooms.filter(room => originalFloorIds.has(room.floorId));
      const originalRoomIds = new Set(originalRooms.map(room => room.id));
      const draftFloorIds = new Set(draftFloors.map(floor => floor.id));
      const draftRoomIds = new Set(draftRooms.map(room => room.id));

      if (!showAdd) {
        // Pre-flight: never start removing AC-linked rooms (mirrors the
        // server's 409 guards but refuses before any mutation happens).
        const removedRoomIds = originalRooms.filter(room => !draftRoomIds.has(room.id)).map(room => room.id);
        if (removedRoomIds.length > 0) {
          const originalName = originalBlock?.name ?? name;
          const linked = await linkedRoomIds(originalName);
          const blocked = removedRoomIds.filter(roomId => linked.has(roomId));
          if (blocked.length > 0) {
            throw new Error('Linked AC assets prevent deleting their floor or room.');
          }
        }
      }

      // 1. Create new floors first — rooms below resolve against these ids.
      const floorIdMap = new Map<string, string>();
      for (const [index, floor] of draftFloors.entries()) {
        if (originalFloorIds.has(floor.id)) {
          floorIdMap.set(floor.id, floor.id);
          continue;
        }
        const created = await api<{ floor_id: string }>('/api/v1/campus/floors', {
          method: 'POST',
          body: { block_id: blockId, floor_name: floor.name, floor_number: index },
        });
        floorIdMap.set(floor.id, created.floor_id);
      }

      // 2. Delete removed rooms.
      for (const room of originalRooms) {
        if (!draftRoomIds.has(room.id)) {
          await api(`/api/v1/campus/rooms/${enc(room.id)}`, { method: 'DELETE' });
        }
      }

      // 3. Patch kept rooms (rename / retype / move between floors).
      for (const room of draftRooms) {
        if (!originalRoomIds.has(room.id)) continue;
        const before = originalRooms.find(item => item.id === room.id);
        if (!before) continue;
        const floorId = floorIdMap.get(room.floorId) ?? room.floorId;
        const body: Record<string, unknown> = {};
        if (before.name !== room.name) body.room_name = room.name;
        if ((before.type || '') !== (room.type || '')) body.room_type = room.type || null;
        if (before.floorId !== floorId) body.floor_id = floorId;
        if (Object.keys(body).length > 0) {
          await api(`/api/v1/campus/rooms/${enc(room.id)}`, { method: 'PATCH', body });
        }
      }

      // 4. Create new rooms.
      for (const room of draftRooms) {
        if (originalRoomIds.has(room.id)) continue;
        const floorId = floorIdMap.get(room.floorId) ?? room.floorId;
        if (!floorId) continue;
        await api('/api/v1/campus/rooms', {
          method: 'POST',
          body: { floor_id: floorId, room_name: room.name, room_type: room.type || undefined },
        });
      }

      // 5. Patch kept floors (rename / reorder via floor_number).
      for (const [index, floor] of draftFloors.entries()) {
        if (!originalFloorIds.has(floor.id)) continue;
        const before = originalFloors.find(item => item.id === floor.id);
        if (!before) continue;
        const body: Record<string, unknown> = {};
        if (before.name !== floor.name) body.floor_name = floor.name;
        if ((before.order ?? index) !== index) body.floor_number = index;
        if (Object.keys(body).length > 0) {
          await api(`/api/v1/campus/floors/${enc(floor.id)}`, { method: 'PATCH', body });
        }
      }

      // 6. Delete removed floors (now childless).
      for (const floor of originalFloors) {
        if (!draftFloorIds.has(floor.id)) {
          await api(`/api/v1/campus/floors/${enc(floor.id)}`, { method: 'DELETE' });
        }
      }

      await refreshAll();
      notify('success', showAdd ? 'Block added successfully.' : 'Block updated successfully.');
      closeEditor();
    } catch (error: unknown) {
      // Partial sequences are possible on failure — resync so the UI shows
      // exactly what the server holds.
      await refreshAll();
      notify('error', error instanceof Error ? error.message : 'Saving failed.');
    } finally {
      setSaving(false);
    }
  };

  /** Cascade delete: rooms → floors → block, refusing while ACs remain. */
  const deleteBlock = async () => {
    if (isViewer || !deleteTarget) return;
    const target = deleteTarget;
    setSaving(true);
    try {
      const blockFloors = data.floors.filter(floor => floor.buildingId === target.id);
      const floorIds = new Set(blockFloors.map(floor => floor.id));
      const blockRooms = data.rooms.filter(room => floorIds.has(room.floorId));

      if (blockRooms.length > 0) {
        const linked = await linkedRoomIds(target.name);
        const blocked = blockRooms.filter(room => linked.has(room.id));
        if (blocked.length > 0) {
          notify(
            'error',
            `${target.name} cannot be deleted because ${blocked.length} linked asset(s) still exist.`,
          );
          setDeleteTarget(null);
          return;
        }
      }

      for (const room of blockRooms) {
        await api(`/api/v1/campus/rooms/${enc(room.id)}`, { method: 'DELETE' });
      }
      for (const floor of blockFloors) {
        await api(`/api/v1/campus/floors/${enc(floor.id)}`, { method: 'DELETE' });
      }
      await api(`/api/v1/campus/blocks/${enc(target.id)}`, { method: 'DELETE' });

      await refreshAll();
      setDeleteTarget(null);
      notify('success', `${target.name} and its nested locations were removed.`);
    } catch (error: unknown) {
      await refreshAll();
      notify('error', error instanceof Error ? error.message : 'Delete failed.');
      setDeleteTarget(null);
    } finally {
      setSaving(false);
    }
  };

  const validateFile = (file: File) => {
    const validTypes = ['image/jpeg', 'image/png', 'image/webp'];
    if (!validTypes.includes(file.type)) return 'Only JPG, JPEG, PNG, and WEBP photos are accepted.';
    if (file.size > 5 * 1024 * 1024) return 'Each photo must be 5 MB or smaller.';
    return '';
  };

  // Photos upload straight to storage (signed upload → signed read URL);
  // nothing is stored in localStorage or the DB anymore.
  const uploadPhotos = async (files: FileList | File[]) => {
    if (!draft || editorMode === 'view') return;
    if (!editingBuildingId) {
      setUploadError('Save the block first, then add photos.');
      return;
    }
    const queue = Array.from(files);
    const invalid = queue.map(validateFile).find(Boolean);
    if (invalid) {
      setUploadError(invalid);
      return;
    }
    setUploadError('');
    setUploading(true);
    let completed = 0;
    try {
      for (const file of queue) {
        const form = new FormData();
        form.append('file', file);
        const entry = await apiFormData<CampusPhoto>(
          `/api/v1/campus/blocks/${enc(editingBuildingId)}/photos`,
          form,
        );
        setDraft(current => (current
          ? {
            ...current,
            photos: [...current.photos, {
              id: entry.id,
              name: entry.name ?? file.name,
              dataUrl: entry.signed_url ?? '',
              isCover: Boolean(entry.is_cover),
            }],
          }
          : current));
        completed += 1;
        setUploadProgress(Math.round((completed / queue.length) * 100));
      }
      invalidateCampusTreeCache();
      void qc.invalidateQueries({ queryKey: ['campus'] });
    } catch (error: unknown) {
      setUploadError(error instanceof Error ? error.message : 'Could not upload the photos.');
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const removePhoto = async (photo: BuildingPhoto) => {
    if (!draft) return;
    if (!editingBuildingId) {
      setDraft({ ...draft, photos: draft.photos.filter(item => item.id !== photo.id) });
      return;
    }
    try {
      await api(`/api/v1/campus/blocks/${enc(editingBuildingId)}/photos/${enc(photo.id)}`, {
        method: 'DELETE',
      });
      setDraft(current => (current ? { ...current, photos: current.photos.filter(item => item.id !== photo.id) } : current));
      invalidateCampusTreeCache();
      void qc.invalidateQueries({ queryKey: ['campus'] });
    } catch (error: unknown) {
      notify('error', error instanceof Error ? error.message : 'Could not remove the photo.');
    }
  };

  const onFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) void uploadPhotos(event.target.files);
    event.target.value = '';
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    void uploadPhotos(event.dataTransfer.files);
  };

  const coverPhoto = (building: Building) => (building.photos || []).find(photo => photo.isCover) || building.photos?.[0];
  const totalRooms = (buildingId: string) => {
    const floorIds = data.floors.filter(floor => floor.buildingId === buildingId).map(floor => floor.id);
    return data.rooms.filter(room => floorIds.includes(room.floorId)).length;
  };
  const currentRooms = useMemo(() => draftRooms, [draftRooms]);

  if (isLoading) return <div className="flex items-center justify-center py-20 text-sm text-slate-500">Loading campus data...</div>;

  return (
    <div className="flex flex-col gap-6 pb-8">
      {toast && <div className={`fixed right-6 top-20 z-[70] rounded-lg border px-4 py-3 text-sm font-medium shadow-lg ${toast.type === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'}`}>{toast.message}</div>}

      <div className="flex items-center justify-between gap-4">
        <div><h1 className="text-2xl font-bold text-slate-900">Campus Hierarchy</h1><p className="text-sm text-slate-500 mt-1">Manage campus blocks, floors, departments, and rooms.</p></div>
        {!isViewer && <button onClick={() => openEditor(null, 'edit')} className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm transition-colors"><Plus size={16} /> Add Block</button>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Buildings', count: data.buildings.length, icon: Building2, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-100' },
          { label: 'Floors', count: data.floors.length, icon: Layers, color: 'text-purple-600', bg: 'bg-purple-50 border-purple-100' },
          { label: 'Departments', count: data.departments.length, icon: Building2, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-100' },
          { label: 'Rooms', count: data.rooms.length, icon: Grid3X3, color: 'text-amber-600', bg: 'bg-amber-50 border-amber-100' },
        ].map(item => { const Icon = item.icon; return <div key={item.label} className={`rounded-xl border p-4 flex items-center gap-3 ${item.bg}`}><div className={`w-10 h-10 rounded-full bg-white flex items-center justify-center shadow-sm ${item.color} border`}><Icon size={18} /></div><div><p className={`text-2xl font-bold ${item.color}`}>{item.count}</p><p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{item.label}</p></div></div>; })}
      </div>

      <div className="flex bg-slate-100 rounded-xl p-1 gap-1 w-fit">{((canManageCampus ? ['buildings', 'departments', 'rooms', 'location'] : ['buildings', 'departments', 'rooms']) as CampusTab[]).map(tab => <button key={tab} onClick={() => setActiveTab(tab)} className={`px-5 py-2 rounded-lg text-sm font-semibold capitalize transition-all ${activeTab === tab ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>{tab}</button>)}</div>

      {activeTab === 'buildings' && <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {data.buildings.map((building, index) => {
          const photo = coverPhoto(building);
          const floorCount = data.floors.filter(floor => floor.buildingId === building.id).length;
          return <motion.div key={building.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.04 }} className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 hover:shadow-md transition-all">
            <div className="flex items-start justify-between mb-3"><div className="w-14 h-14 rounded-xl overflow-hidden flex items-center justify-center border" style={{ backgroundColor: `${building.layout?.color || '#64748b'}20`, borderColor: `${building.layout?.color || '#64748b'}40` }}>{photo ? <img src={photo.dataUrl} alt={building.name} className="h-full w-full object-cover" /> : <Building2 size={22} style={{ color: building.layout?.color || '#64748b' }} />}</div><div className="flex items-center gap-1"><button onClick={() => openEditor(building, 'view')} className="p-2 rounded-md hover:bg-slate-100 text-slate-600" title="View block"><Eye size={15} /></button>{!isViewer && <><button onClick={() => openEditor(building, 'edit')} className="p-2 rounded-md hover:bg-blue-100 text-blue-600" title="Edit block"><Pencil size={15} /></button><button onClick={() => setDeleteTarget(building)} className="p-2 rounded-md hover:bg-red-100 text-red-600" title="Delete block"><Trash2 size={15} /></button></>}<MoreVertical size={16} className="ml-1 text-slate-400" /></div></div>
            <div className="flex items-start justify-between gap-2"><div><p className="font-semibold text-slate-900 text-sm">{building.name}</p><p className="text-xs text-slate-500 mt-1 font-mono">{building.id}</p></div></div>
            <div className="flex items-center gap-3 mt-3 text-xs text-slate-400"><span className="flex items-center gap-1"><Layers size={11} /> {floorCount} Floors</span><span className="flex items-center gap-1"><Grid3X3 size={11} /> {totalRooms(building.id)} Rooms</span></div>
          </motion.div>;
        })}
        {data.buildings.length === 0 && <div className="col-span-full text-center py-12 text-slate-400 bg-white rounded-xl border border-slate-200"><Building2 size={32} className="mx-auto mb-2 opacity-30" /><p className="font-medium text-sm">No blocks yet. Add the first block to get started.</p></div>}
      </div>}

      {activeTab === 'departments' && <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden"><table className="w-full text-sm"><thead className="bg-slate-50 border-b border-slate-200"><tr>{['Dept. ID', 'Name'].map(header => <th key={header} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">{header}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{data.departments.map(department => <tr key={department.id}><td className="px-4 py-3 font-mono text-sm text-blue-700 font-semibold">{department.id}</td><td className="px-4 py-3 font-medium text-slate-900">{department.name}</td></tr>)}</tbody></table></div>}
      {activeTab === 'rooms' && <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden"><table className="w-full text-sm"><thead className="bg-slate-50 border-b border-slate-200"><tr>{['Room ID', 'Name', 'Type', 'Floor'].map(header => <th key={header} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">{header}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{data.rooms.map(room => <tr key={room.id}><td className="px-4 py-3 font-mono text-sm text-blue-700 font-semibold">{room.id}</td><td className="px-4 py-3 font-medium text-slate-900">{room.name}</td><td className="px-4 py-3 text-slate-500">{room.type || '—'}</td><td className="px-4 py-3 text-slate-500">{room.floorId}</td></tr>)}</tbody></table></div>}
      {activeTab === 'location' && canManageCampus && <LocationReviewPanel />}

      {deleteTarget && !isViewer && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden"><div className="p-5 border-b border-red-100 bg-red-50"><h3 className="font-bold text-lg text-red-800">Delete {deleteTarget.name}?</h3><p className="text-sm text-red-700 mt-1">This will remove {data.floors.filter(floor => floor.buildingId === deleteTarget.id).length} floor(s), {totalRooms(deleteTarget.id)} room(s), and their records.</p></div><div className="p-5 text-sm text-slate-600">Linked AC assets are checked before deletion. The block will stay intact if any assets still point to it.</div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={() => setDeleteTarget(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={deleteBlock} disabled={saving} className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium flex items-center gap-1.5 disabled:opacity-50">{saving ? 'Deleting...' : <><Trash2 size={14} /> Delete Block</>}</button></div></div></div>}

      {editorOpen && draft && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[92vh] overflow-hidden flex flex-col"><div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between"><div><h3 className="font-bold text-lg">{showAdd ? 'Add Block' : editorMode === 'view' ? 'Block Details' : 'Edit Block'}</h3><p className="text-xs text-slate-500 mt-1">{draft.name || 'New campus block'}</p></div><button onClick={closeEditor} className="p-2 rounded-full hover:bg-slate-200" title="Close"><X size={18} /></button></div><div className="overflow-y-auto p-6 space-y-6">
        <fieldset disabled={editorMode === 'view' || saving} className="space-y-4"><div className="grid grid-cols-1 md:grid-cols-2 gap-4"><div><label className="text-xs font-semibold text-slate-500 uppercase">Block name</label><input value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} className={`mt-1 w-full border rounded-lg px-3 py-2 text-sm ${errors.name ? 'border-red-400' : 'border-slate-200'}`} placeholder="Academic Block A" />{errors.name && <p className="text-xs text-red-600 mt-1">{errors.name}</p>}</div><div><label className="text-xs font-semibold text-slate-500 uppercase">Block code</label><input value={draft.id} disabled={!showAdd} onChange={event => setDraft({ ...draft, id: event.target.value.toUpperCase() })} className={`mt-1 w-full border rounded-lg px-3 py-2 text-sm bg-white disabled:bg-slate-50 disabled:text-slate-500 ${errors.id ? 'border-red-400' : 'border-slate-200'}`} placeholder="BLK-001" />{errors.id && <p className="text-xs text-red-600 mt-1">{errors.id}</p>}{!showAdd && <p className="text-[11px] text-slate-400 mt-1">The block code is fixed after creation.</p>}</div></div></fieldset>

        <section className="border-t border-slate-100 pt-5"><div className="flex items-center justify-between mb-3"><div><h4 className="font-bold text-slate-800">Floors and rooms</h4><p className="text-xs text-slate-500">Reorder floors and manage rooms inside each floor.</p></div>{editorMode !== 'view' && <button onClick={addFloor} className="text-xs font-semibold text-blue-600 flex items-center gap-1"><Plus size={14} /> Add floor</button>}</div>{errors.floors && <p className="text-xs text-red-600 mb-2">{errors.floors}</p>}<div className="space-y-3">{draftFloors.map((floor, index) => <div key={floor.id} className="rounded-lg border border-slate-200 p-3"><div className="flex items-center gap-2"><input disabled={editorMode === 'view' || saving} value={floor.name} onChange={event => setDraftFloors(current => current.map(item => item.id === floor.id ? { ...item, name: event.target.value } : item))} className="flex-1 border border-slate-200 rounded-md px-2 py-1.5 text-sm font-medium" /><span className="text-xs text-slate-400">{draftRooms.filter(room => room.floorId === floor.id).length} rooms</span>{editorMode !== 'view' && <><button onClick={() => moveFloor(index, -1)} disabled={index === 0} className="p-1.5 text-slate-500 disabled:opacity-30" title="Move floor up"><ArrowUp size={14} /></button><button onClick={() => moveFloor(index, 1)} disabled={index === draftFloors.length - 1} className="p-1.5 text-slate-500 disabled:opacity-30" title="Move floor down"><ArrowDown size={14} /></button><button onClick={() => deleteFloor(floor.id)} className="p-1.5 text-red-500" title="Delete floor"><Trash2 size={14} /></button><button onClick={() => addRoom(floor.id)} className="p-1.5 text-blue-600" title="Add room"><Plus size={14} /></button></>}</div><div className="mt-2 space-y-2 pl-4">{currentRooms.filter(room => room.floorId === floor.id).map(room => <div key={room.id} className="flex items-center gap-2"><input disabled={editorMode === 'view' || saving} value={room.name} onChange={event => setDraftRooms(current => current.map(item => item.id === room.id ? { ...item, name: event.target.value } : item))} className="flex-1 border border-slate-200 rounded-md px-2 py-1.5 text-sm" /><select disabled={editorMode === 'view' || saving} value={room.type} onChange={event => setDraftRooms(current => current.map(item => item.id === room.id ? { ...item, type: event.target.value } : item))} className="border border-slate-200 rounded-md px-2 py-1.5 text-xs bg-white"><option value="">—</option><option>Classroom</option><option>Lab</option><option>Office</option><option>Hall</option><option>Server Room</option></select>{editorMode !== 'view' && <button onClick={() => setDraftRooms(current => current.filter(item => item.id !== room.id))} className="p-1.5 text-red-500" title="Remove room"><Trash2 size={13} /></button>}</div>)}</div></div>)}</div></section>

        <section className="border-t border-slate-100 pt-5"><div className="flex items-center justify-between mb-3"><div><h4 className="font-bold text-slate-800">Block photos</h4><p className="text-xs text-slate-500">JPG, PNG, or WEBP up to 5 MB each. The cover appears on the card.</p></div><ImagePlus size={18} className="text-slate-400" /></div>{editorMode !== 'view' && <div onDragOver={event => event.preventDefault()} onDrop={onDrop} className="border-2 border-dashed border-slate-200 rounded-lg p-5 text-center hover:border-blue-400 transition-colors"><Upload size={20} className="mx-auto text-slate-400 mb-2" /><p className="text-sm text-slate-600">{showAdd ? 'Save the block first, then add photos.' : <>Drag photos here or <label className="text-blue-600 font-semibold cursor-pointer">choose files<input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={onFileInput} className="hidden" /></label></>}</p>{uploading && <p className="text-xs text-blue-600 mt-2">Uploading {uploadProgress}%</p>}{uploadError && <p className="text-xs text-red-600 mt-2">{uploadError}</p>}</div>}<div className="grid grid-cols-3 sm:grid-cols-5 gap-3 mt-3">{draft.photos.map(photo => <div key={photo.id} className={`relative aspect-square rounded-lg overflow-hidden border-2 ${photo.isCover ? 'border-blue-500' : 'border-slate-200'}`}><button onClick={() => setLightbox(photo)} className="h-full w-full"><img src={photo.dataUrl} alt={photo.name} className="h-full w-full object-cover" /></button>{photo.isCover && <span className="absolute left-1 top-1 rounded bg-blue-600 px-1.5 py-0.5 text-[10px] text-white">Cover</span>}{editorMode !== 'view' && <div className="absolute bottom-1 right-1 flex gap-1"><button onClick={() => void removePhoto(photo)} className="rounded bg-white/90 px-1.5 py-1 text-[10px] text-red-700">Remove</button></div>}</div>)}</div></section>
      </div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={closeEditor} disabled={saving} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Close</button>{editorMode === 'view' ? !isViewer && <button onClick={() => setEditorMode('edit')} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium flex items-center gap-1.5"><Pencil size={14} /> Edit Block</button> : <button onClick={saveBlock} disabled={saving || uploading} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-50">{saving ? 'Saving...' : showAdd ? 'Add Block' : 'Save Changes'}</button>}</div></div></div>}

      {lightbox && <div className="fixed inset-0 bg-black/80 z-[80] flex items-center justify-center p-6" onClick={() => setLightbox(null)}><button className="absolute right-5 top-5 text-white" onClick={() => setLightbox(null)}><X size={26} /></button><img src={lightbox.dataUrl} alt={lightbox.name} className="max-h-[85vh] max-w-[90vw] object-contain" onClick={event => event.stopPropagation()} /></div>}
    </div>
  );
}
