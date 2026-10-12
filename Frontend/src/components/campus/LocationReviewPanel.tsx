'use client';

/**
 * Location review section (§13) — the campus page's "location review" tab
 * for admin/manager. Three pieces, closing the source-vs-confirmed loop:
 *
 *  1. Review queue: assets whose preserved source text has no confirmed id
 *     yet, one chip per level needing review (source verbatim + status).
 *  2. Alias directory: map a text variant to exactly one canonical entity so
 *     the resolver can finish those rows; active-alias repoints are 409 and
 *     surface here as an error instead of silently re-mapping.
 *  3. Re-resolve: one idempotent batch pass (§12) — reports scanned/updated.
 *
 * Manual per-level confirmation lives on the asset detail (EDIT_ASSET);
 * every row here links to it via roleAssetDetail.
 */
import React, { useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';

import { LOCATION_LEVEL_KEYS, locationLevelText, locationReviewLevels } from '@/lib/adapters/asset';
import { useCampusTree } from '@/lib/api/campus';
import {
  useCreateLocationAlias,
  useDeleteLocationAlias,
  useLocationAliases,
  useLocationReview,
  useReResolveLocations,
} from '@/lib/api/location';
import type { LocationLevelKey } from '@/lib/types/asset';
import { ApiError } from '@/lib/api/client';
import { roleAssetDetail } from '@/lib/permissions';
import { useAuth } from '@/contexts/AuthContext';

const LEVEL_LABELS: Record<LocationLevelKey, string> = {
  college: 'College',
  block: 'Block',
  floor: 'Floor',
  department: 'Department',
  room: 'Room',
};

const STATUS_CHIP: Record<string, string> = {
  RESOLVED: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  UNRESOLVED: 'bg-amber-100 text-amber-800 border-amber-200',
  AMBIGUOUS: 'bg-orange-100 text-orange-800 border-orange-200',
  MISSING: 'bg-slate-100 text-slate-500 border-slate-200',
};

const chip = (status: string): string => STATUS_CHIP[status] ?? STATUS_CHIP.MISSING;

/** Overall (extended) status: ASSIGNED settles it; PARTIAL still shows gaps. */
const OVERALL_CHIP: Record<string, string> = {
  ASSIGNED: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  PARTIAL: 'bg-amber-100 text-amber-800 border-amber-200',
  PENDING: 'bg-slate-100 text-slate-500 border-slate-200',
};

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

interface Target {
  id: string;
  label: string;
}

export default function LocationReviewPanel() {
  const { user } = useAuth();

  // ── Review queue state ──────────────────────────────────────────────────
  const [page, setPage] = useState(1);
  const [level, setLevel] = useState<LocationLevelKey | ''>('');
  const review = useLocationReview({ page, level: level || null });

  // ── Alias state ─────────────────────────────────────────────────────────
  const [includeInactive, setIncludeInactive] = useState(false);
  const aliases = useLocationAliases(includeInactive);
  const createAlias = useCreateLocationAlias();
  const deleteAlias = useDeleteLocationAlias();
  const [entityType, setEntityType] = useState<LocationLevelKey>('room');
  const [targetId, setTargetId] = useState('');
  const [aliasText, setAliasText] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);
  // Review rows the last-created alias will finish (null = no alias created
  // yet this session): makes the required re-resolution step obvious (§11).
  const [aliasMatchCount, setAliasMatchCount] = useState<number | null>(null);

  // ── Re-resolve ──────────────────────────────────────────────────────────
  const reResolve = useReResolveLocations();
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);

  const treeQuery = useCampusTree();
  const targets = useMemo<Target[]>(() => {
    const tree = treeQuery.data;
    if (!tree) return [];
    const blocks = [...tree.colleges.flatMap((c) => c.blocks), ...tree.orphan_blocks];
    switch (entityType) {
      case 'college':
        return tree.colleges.map((c) => ({ id: c.college_id, label: c.college_name }));
      case 'block':
        return blocks.map((b) => ({ id: b.block_id, label: b.block_name }));
      case 'floor':
        return blocks.flatMap((b) =>
          b.floors.map((f) => ({ id: f.floor_id, label: `${b.block_name} / ${f.floor_name}` })),
        );
      case 'room':
        return blocks.flatMap((b) =>
          b.floors.flatMap((f) =>
            f.rooms.map((r) => ({
              id: r.room_id,
              label: `${b.block_name} / ${f.floor_name} / ${r.room_name}`,
            })),
          ),
        );
      case 'department':
        return tree.departments.map((d) => ({ id: d.department_id, label: d.department_name }));
      default:
        return [];
    }
  }, [treeQuery.data, entityType]);

  const onCreateAlias = (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    setFormOk(null);
    setAliasMatchCount(null);
    const trimmed = aliasText.trim();
    if (!targetId || !trimmed) {
      setFormError('Pick a canonical target and enter the alias text.');
      return;
    }
    createAlias.mutate(
      { entity_type: entityType, entity_id: targetId, alias: trimmed },
      {
        onSuccess: (row) => {
          setAliasText('');
          setFormOk(`“${row.alias}” now resolves to ${row.entity_id}.`);
          setAliasMatchCount(row.matching_review_rows ?? 0);
          setTargetId('');
        },
        onError: (err) =>
          setFormError(
            errorMessage(
              err,
              'Could not save the alias — an active alias may already point elsewhere (409).',
            ),
          ),
      },
    );
  };

  const onDeleteAlias = (id: number) => {
    setFormError(null);
    deleteAlias.mutate(id, { onError: (err) => setFormError(errorMessage(err, 'Could not delete the alias.')) });
  };

  const onReResolve = () => {
    setResolveMsg(null);
    setFormError(null);
    // 2000 = the endpoint's max batch; the test/dev databases hold far fewer
    // pending rows, so one pass settles the whole queue.
    reResolve.mutate(2000, {
      onSuccess: (result) =>
        setResolveMsg(`Scanned ${result.scanned} pending row${result.scanned === 1 ? '' : 's'}, updated ${result.updated}.`),
      onError: (err) => setFormError(errorMessage(err, 'Re-resolution failed.')),
    });
  };

  const pagination = review.data?.pagination;
  const items = review.data?.items ?? [];

  return (
    <div className="space-y-6">
      {/* ── Re-resolve header ─────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-1.5 text-sm font-bold text-slate-700">
              <Search size={14} className="text-indigo-500" /> Location review
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">
              Imported text that matched no canonical location: confirm it on the asset page, teach it as an alias, or
              re-run resolution — source text is never discarded, manual confirmations are never overwritten.
            </p>
          </div>
          <button
            type="button"
            onClick={onReResolve}
            disabled={reResolve.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-60"
          >
            <RefreshCw size={13} className={reResolve.isPending ? 'animate-spin' : ''} />
            {reResolve.isPending ? 'Re-resolving…' : 'Re-resolve pending'}
          </button>
        </div>
        {resolveMsg && <p className="mt-2 text-xs font-semibold text-emerald-600">{resolveMsg}</p>}
        {formError && (
          <p className="mt-2 flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs font-semibold text-red-700">
            <AlertTriangle size={12} /> {formError}
          </p>
        )}
      </div>

      {/* ── Review queue ──────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4">
          <h4 className="text-xs font-bold uppercase text-slate-400">Queue (source without confirmation)</h4>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
            Level
            <select
              value={level}
              onChange={(e) => {
                setLevel(e.target.value as LocationLevelKey | '');
                setPage(1);
              }}
              className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700"
            >
              <option value="">All</option>
              {LOCATION_LEVEL_KEYS.map((key) => (
                <option key={key} value={key}>
                  {LEVEL_LABELS[key]}
                </option>
              ))}
            </select>
          </label>
        </div>

        {review.isError && (
          <p className="p-4 text-xs font-semibold text-red-600">Could not load the review queue — retry shortly.</p>
        )}
        {review.isLoading && <p className="p-4 text-xs text-slate-400">Loading…</p>}

        {!review.isLoading && items.length === 0 && (
          <p className="p-6 text-center text-sm text-slate-500">
            Queue clear — every asset’s source location is confirmed.
          </p>
        )}

        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-[10px] uppercase text-slate-400">
                  <th className="px-4 py-2 font-bold">Asset</th>
                  <th className="px-4 py-2 font-bold">Needs review</th>
                  <th className="px-4 py-2 font-bold">Overall</th>
                </tr>
              </thead>
              <tbody>
                {items.map((ac) => {
                  const reviewKeys = locationReviewLevels(ac.locationDetail);
                  return (
                    <tr key={ac.id} className="border-b border-slate-50 hover:bg-slate-50/60">
                      <td className="px-4 py-2.5">
                        <Link
                          href={roleAssetDetail(user?.role ?? null, ac.id)}
                          className="font-semibold text-indigo-600 hover:underline"
                        >
                          {ac.acId ?? ac.id}
                        </Link>
                        <p className="text-[10px] text-slate-400">
                          {[ac.make, ac.model].filter(Boolean).join(' ') || '—'}
                        </p>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap gap-1.5">
                          {reviewKeys.map((key) => {
                            const lvl = ac.locationDetail?.levels[key];
                            return (
                              <span
                                key={key}
                                title={lvl?.source ?? ''}
                                className={`inline-flex max-w-[22rem] items-baseline gap-1 rounded-full border px-2 py-0.5 ${chip(lvl?.status ?? 'MISSING')}`}
                              >
                                <span className="font-bold">{LEVEL_LABELS[key]}</span>
                                <span className="truncate font-mono">“{locationLevelText(lvl)}”</span>
                              </span>
                            );
                          })}
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${OVERALL_CHIP[ac.locationDetail?.status ?? 'PENDING'] ?? OVERALL_CHIP.PENDING}`}
                        >
                          {ac.locationDetail?.status ?? '—'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pagination && pagination.total_pages > 1 && (
          <div className="flex items-center justify-between border-t border-slate-100 p-3 text-xs text-slate-500">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="rounded-lg border border-slate-200 px-3 py-1.5 font-semibold disabled:opacity-40"
            >
              Previous
            </button>
            <span>
              Page {pagination.page} of {pagination.total_pages} · {pagination.total} pending
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(pagination.total_pages, p + 1))}
              disabled={page >= pagination.total_pages}
              className="rounded-lg border border-slate-200 px-3 py-1.5 font-semibold disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {/* ── Alias directory ───────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4">
          <h4 className="text-xs font-bold uppercase text-slate-400">Aliases (text variant → canonical)</h4>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="accent-indigo-600"
            />
            Show deactivated
          </label>
        </div>

        <form onSubmit={onCreateAlias} className="flex flex-wrap items-end gap-2 border-b border-slate-100 p-4">
          <label className="text-[10px] font-bold uppercase text-slate-400">
            Type
            <select
              value={entityType}
              onChange={(e) => {
                setEntityType(e.target.value as LocationLevelKey);
                setTargetId('');
              }}
              className="mt-1 block w-32 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-normal text-slate-700"
            >
              {LOCATION_LEVEL_KEYS.map((key) => (
                <option key={key} value={key}>
                  {LEVEL_LABELS[key]}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[10px] font-bold uppercase text-slate-400">
            Canonical target
            <select
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              className="mt-1 block w-64 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-normal text-slate-700"
            >
              <option value="">Choose…</option>
              {targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[10px] font-bold uppercase text-slate-400">
            Alias text
            <input
              value={aliasText}
              onChange={(e) => setAliasText(e.target.value)}
              placeholder="e.g. Main Block (old name)"
              className="mt-1 block w-56 rounded-lg border border-slate-200 px-2 py-1.5 text-xs font-normal text-slate-700"
            />
          </label>
          <button
            type="submit"
            disabled={createAlias.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-60"
          >
            <Plus size={13} /> Add alias
          </button>
          {formOk && <p className="w-full text-xs font-semibold text-emerald-600">{formOk}</p>}
          {formOk && aliasMatchCount !== null && (
            <p className="w-full text-xs text-slate-600">
              {aliasMatchCount > 0 ? (
                <>
                  {aliasMatchCount} review row{aliasMatchCount === 1 ? '' : 's'} match{aliasMatchCount === 1 ? 'es' : ''} this
                  alias —{' '}
                  <button
                    type="button"
                    onClick={onReResolve}
                    disabled={reResolve.isPending}
                    className="font-bold text-indigo-600 underline hover:text-indigo-800 disabled:opacity-60"
                  >
                    re-resolve now
                  </button>{' '}
                  to apply it (nothing changes until you do).
                </>
              ) : (
                'No pending rows match this alias — nothing to re-resolve.'
              )}
            </p>
          )}
        </form>

        {aliases.isError && (
          <p className="p-4 text-xs font-semibold text-red-600">Could not load aliases — retry shortly.</p>
        )}
        {aliases.isLoading && <p className="p-4 text-xs text-slate-400">Loading…</p>}
        {(aliases.data?.length ?? 0) === 0 && !aliases.isLoading && (
          <p className="p-6 text-center text-sm text-slate-500">No aliases yet.</p>
        )}

        {(aliases.data?.length ?? 0) > 0 && (
          <ul className="divide-y divide-slate-50">
            {(aliases.data ?? []).map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${chip(row.is_active ? 'RESOLVED' : 'MISSING')}`}>
                    {row.entity_type}
                  </span>
                  <span className={`font-mono ${row.is_active ? 'text-slate-700' : 'text-slate-400 line-through'}`}>
                    “{row.alias}”
                  </span>
                  <span className="text-slate-400">→</span>
                  <span className="font-semibold text-slate-700">{row.entity_id}</span>
                  {!row.is_active && <span className="text-[10px] font-bold uppercase text-slate-400">deactivated</span>}
                </div>
                {row.is_active && (
                  <button
                    type="button"
                    onClick={() => onDeleteAlias(row.id)}
                    disabled={deleteAlias.isPending}
                    title="Deactivate alias"
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
