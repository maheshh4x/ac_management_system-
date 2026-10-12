'use client';

import React, { useEffect, useState } from 'react';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { getBuildings, invalidateCampusTreeCache } from '@/lib/campus-services';
import { useUpdateBlockLayout } from '@/lib/api/campus';
import { Building } from '@/lib/types/campus';
import { LayoutGrid, Loader2, Save, X } from 'lucide-react';

/**
 * Phase 3 task 5 — map layout editing. Blocks carry a free-form `layout`
 * document (`{x, y, w, h, color}` — the contract `BuildingBlock` renders
 * from) persisted through PATCH /api/v1/campus/blocks/<id>/layout
 * (JSON-validated, 256 KB capped). Gated by MANAGE_CAMPUS.
 */

interface LayoutDraft {
  blockId: string;
  name: string;
  x: string;
  y: string;
  w: string;
  h: string;
  color: string;
}

const DEFAULT_COLOR = '#64748b';

function readLayout(building: Building, index: number): Record<string, unknown> {
  const raw = building.layout as unknown;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  // Grid fallback for blocks that have never been placed on the map.
  return { x: ((index % 5) - 2) * 7, y: (Math.floor(index / 5) - 1) * 7, w: 5, h: 5, color: DEFAULT_COLOR };
}

function toDraft(building: Building, index: number): LayoutDraft {
  const layout = readLayout(building, index);
  const num = (value: unknown, fallback: number | string) =>
    typeof value === 'number' && Number.isFinite(value) ? String(value) : String(fallback);
  return {
    blockId: building.id,
    name: building.name,
    x: num(layout.x, ((index % 5) - 2) * 7),
    y: num(layout.y, (Math.floor(index / 5) - 1) * 7),
    w: num(layout.w, 5),
    h: num(layout.h, 5),
    color: typeof layout.color === 'string' && layout.color ? layout.color : DEFAULT_COLOR,
  };
}

const FIELDS: { key: 'x' | 'y' | 'w' | 'h'; label: string; title: string }[] = [
  { key: 'x', label: 'X', title: 'Position across the map' },
  { key: 'y', label: 'Y', title: 'Position depth on the map' },
  { key: 'w', label: 'W', title: 'Width' },
  { key: 'h', label: 'H', title: 'Height' },
];

export default function LayoutEditorPanel() {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<LayoutDraft[]>([]);
  const [originals, setOriginals] = useState<Map<string, Record<string, unknown>>>(new Map());
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const updateLayout = useUpdateBlockLayout();

  useEffect(() => {
    if (!open) return;
    let mounted = true;
    invalidateCampusTreeCache(); // re-read the tree, not a stale cache entry
    getBuildings()
      .then(buildings => {
        if (!mounted) return;
        setDrafts(buildings.map(toDraft));
        setOriginals(new Map(buildings.map(building => [building.id, readLayout(building, 0)])));
      })
      .catch(() => {
        if (mounted) setMessage({ type: 'error', text: 'Could not load blocks.' });
      });
    return () => {
      mounted = false;
    };
  }, [open]);

  const setValue = (blockId: string, key: keyof LayoutDraft, value: string) => {
    setDrafts(current => current.map(draft => (draft.blockId === blockId ? { ...draft, [key]: value } : draft)));
    setMessage(null);
  };

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      let changed = 0;
      for (const draft of drafts) {
        const next = {
          x: Number(draft.x) || 0,
          y: Number(draft.y) || 0,
          w: Number(draft.w) || 0,
          h: Number(draft.h) || 0,
          color: draft.color || DEFAULT_COLOR,
        };
        const before = originals.get(draft.blockId) ?? {};
        const keys: (keyof typeof next)[] = ['x', 'y', 'w', 'h', 'color'];
        const differs = keys.some(key => before[key] !== next[key]);
        if (!differs) continue;
        // Merge so any extra keys already stored on the block survive.
        await updateLayout.mutateAsync({
          blockId: draft.blockId,
          layout: { ...before, ...next },
        });
        changed += 1;
      }
      invalidateCampusTreeCache();
      if (changed === 0) {
        setMessage({ type: 'success', text: 'No changes to save.' });
      } else {
        setMessage({ type: 'success', text: `Saved layout for ${changed} block(s).` });
        // Refresh originals so a second save only sends what changed again.
        invalidateCampusTreeCache();
        const buildings = await getBuildings();
        setOriginals(new Map(buildings.map(building => [building.id, readLayout(building, 0)])));
      }
    } catch (error: unknown) {
      invalidateCampusTreeCache();
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : 'Saving the layout failed.',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProtectedAction permission="MANAGE_CAMPUS">
      <button
        onClick={() => {
          setMessage(null);
          setOpen(true);
        }}
        className="pointer-events-auto flex items-center gap-2 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm backdrop-blur-md transition-colors hover:bg-white"
        title="Edit map layout"
      >
        <LayoutGrid size={14} /> Edit layout
      </button>

      {open && (
        <div className="pointer-events-auto fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 p-5">
              <div>
                <h3 className="text-lg font-bold text-slate-900">Map layout</h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Position each block on the 3D map. Saved through the campus layout endpoint.
                </p>
              </div>
              <button
                onClick={() => setOpen(false)}
                disabled={saving}
                className="rounded-full p-2 text-slate-500 transition-colors hover:bg-slate-200"
                title="Close"
              >
                <X size={18} />
              </button>
            </div>

            <div className="max-h-[60vh] space-y-3 overflow-y-auto p-5">
              {drafts.length === 0 && !message && (
                <p className="py-6 text-center text-sm text-slate-400">Loading blocks…</p>
              )}
              {drafts.map(draft => (
                <div key={draft.blockId} className="rounded-lg border border-slate-200 p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-semibold text-slate-800">{draft.name}</p>
                    <span className="font-mono text-xs text-slate-400">{draft.blockId}</span>
                  </div>
                  <div className="flex flex-wrap items-end gap-2">
                    {FIELDS.map(field => (
                      <label key={field.key} className="flex flex-col gap-0.5">
                        <span className="text-[10px] font-bold uppercase text-slate-400" title={field.title}>
                          {field.label}
                        </span>
                        <input
                          type="number"
                          step="0.5"
                          value={draft[field.key]}
                          onChange={event => setValue(draft.blockId, field.key, event.target.value)}
                          className="w-20 rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                        />
                      </label>
                    ))}
                    <label className="flex flex-col gap-0.5">
                      <span className="text-[10px] font-bold uppercase text-slate-400">Color</span>
                      <input
                        type="color"
                        value={draft.color}
                        onChange={event => setValue(draft.blockId, 'color', event.target.value)}
                        className="h-8 w-14 cursor-pointer rounded-md border border-slate-200 bg-white p-0.5"
                        title="Block color"
                      />
                    </label>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-center justify-between gap-3 border-t border-slate-100 p-5">
              <div className="text-xs font-medium">
                {message && (
                  <span className={message.type === 'success' ? 'text-emerald-600' : 'text-red-600'}>
                    {message.text}
                  </span>
                )}
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setOpen(false)}
                  disabled={saving}
                  className="rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700"
                >
                  Close
                </button>
                <button
                  onClick={() => void save()}
                  disabled={saving}
                  className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                  {saving ? 'Saving…' : 'Save layout'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </ProtectedAction>
  );
}
