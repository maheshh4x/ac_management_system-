'use client';

import React, { useState } from 'react';
import { useACDataset } from '@/hooks/useACDataset';
import { useAssetQueryState } from '@/hooks/useAssetQueryState';
import { useAssetFacets } from '@/lib/api/assets';
import { ACFilterBar } from '@/components/features/ACFilterBar';
import { ACTable } from '@/components/features/ACTable';
import { ImportACModal } from '@/components/features/csv-import/ImportACModal';
import { AssetFormModal } from '@/components/features/assets/AssetFormModal';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { Upload, Plus, AlertTriangle } from 'lucide-react';
import { inlineDraftToPayload } from '@/lib/adapters/asset';
import type { ExtendedAC } from '@/lib/types/asset';

export default function AssetsPage() {
  // Filters, page, size and sort all live in the URL query string (P3 t2).
  const query = useAssetQueryState();

  const [showImportModal, setShowImportModal] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editTarget, setEditTarget] = useState<ExtendedAC | null>(null);

  const {
    acs,
    stats,
    loading,
    error,
    pagination,
    refreshData,
    updateACAsset,
    decommissionAC,
  } = useACDataset(query.filters, {
    page: query.page,
    pageSize: query.pageSize,
    sort: query.sort || 'ac_id',
  });

  const { data: facets } = useAssetFacets();
  const buildingList = (facets?.block ?? []).map(b => ({ id: b.value, name: b.value, count: b.count }));

  const handleInlineSave = async (ac: ExtendedAC, draft: Parameters<typeof inlineDraftToPayload>[0]) => {
    await updateACAsset(ac.acId ?? ac.id, inlineDraftToPayload(draft));
  };

  const handleDecommission = async (ac: ExtendedAC) => {
    const ok = window.confirm(
      `Decommission ${ac.id}?\n\nThe asset stays in history but is removed from active lists.`,
    );
    if (!ok) return;
    try {
      await decommissionAC(ac.acId ?? ac.id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Decommission failed.');
    }
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AC Asset Inventory</h1>
          <p className="text-sm text-slate-500 mt-0.5">Manage, filter, and import college AC units across campus blocks.</p>
        </div>

        <ProtectedAction permission="CREATE_AC">
          <div className="flex gap-2">
            <button
              onClick={() => setShowAddModal(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 rounded-xl text-xs font-semibold shadow-2xs transition-colors"
            >
              <Plus size={15} /> Add AC
            </button>
            <button
              onClick={() => setShowImportModal(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs font-semibold shadow-2xs transition-colors"
            >
              <Upload size={15} /> Import College Data
            </button>
          </div>
        </ProtectedAction>
      </div>

      {/* API error banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-xl text-xs font-medium text-red-700">
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      {/* Status Summary Pills */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Total ACs', count: stats?.total ?? 0, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-100' },
          { label: 'Working', count: stats?.working ?? 0, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-100' },
          { label: 'Faulty', count: stats?.faulty ?? 0, color: 'text-red-600', bg: 'bg-red-50 border-red-100' },
          { label: 'Under Maintenance', count: stats?.underMaintenance ?? 0, color: 'text-amber-600', bg: 'bg-amber-50 border-amber-100' },
        ].map(item => (
          <div key={item.label} className={`rounded-xl border p-4 ${item.bg}`}>
            <p className={`text-2xl font-bold ${item.color}`}>{item.count}</p>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mt-1">{item.label}</p>
          </div>
        ))}
      </div>

      {/* Filter Bar */}
      <ACFilterBar
        filters={{ ...query.filters, search: query.searchInput }}
        onChange={query.setFilters}
        onReset={query.reset}
        buildingList={buildingList}
      />

      {/* Assets Table */}
      <ACTable
        acList={acs}
        loading={loading}
        pagination={pagination}
        onPageChange={query.setPage}
        onPageSizeChange={query.setPageSize}
        sort={query.sort || 'ac_id'}
        onSortChange={query.setSort}
        onEdit={setEditTarget}
        onDeactivate={handleDecommission}
        onInlineSave={handleInlineSave}
      />

      {/* Bulk import: upload → dry-run report → confirm → commit */}
      <ImportACModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        onImported={refreshData}
      />

      {/* Create / edit forms */}
      <AssetFormModal
        isOpen={showAddModal}
        mode="create"
        onClose={() => setShowAddModal(false)}
        onSaved={refreshData}
      />
      <AssetFormModal
        isOpen={editTarget !== null}
        mode="edit"
        asset={editTarget}
        onClose={() => setEditTarget(null)}
        onSaved={refreshData}
      />
    </div>
  );
}
