'use client';

import React, { useState } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { useACDataset } from '@/hooks/useACDataset';
import { useAssetQueryState } from '@/hooks/useAssetQueryState';
import { useAssetFacets } from '@/lib/api/assets';
import { ACStatCards } from '@/components/features/ACStatCards';
import { ACTable } from '@/components/features/ACTable';
import { ACFilterBar, STATUSES } from '@/components/features/ACFilterBar';
import { AssetFormModal } from '@/components/features/assets/AssetFormModal';
import { inlineDraftToPayload } from '@/lib/adapters/asset';
import type { ExtendedAC } from '@/lib/types/asset';

export default function AssetsPage() {
  const query = useAssetQueryState();
  const [showAddModal, setShowAddModal] = useState(false);

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

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">AC Asset Management</h1>
          <p className="text-sm text-slate-500 mt-1">Manage, monitor, and update all AC units across campus in real time.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => refreshData()}
            className="flex items-center gap-2 px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold transition-colors"
          >
            <RefreshCw size={14} /> Refresh Data
          </button>
          <ProtectedAction permission="CREATE_AC">
            <button
              onClick={() => setShowAddModal(true)}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl text-xs font-semibold shadow-md transition-all"
            >
              <Plus size={16} /> Add AC Unit
            </button>
          </ProtectedAction>
        </div>
      </div>

      {/* Real-time Status Summary Cards with Gradient design */}
      <ACStatCards
        stats={stats}
        loading={loading}
        error={error}
        onStatClick={status => {
          // KPI-only card ids (Capacity/Depts/…) are not status values —
          // ignoring them keeps the list intact instead of emptying it.
          if (STATUSES.includes(status)) query.setFilters({ status });
        }}
      />

      {/* Filter Bar */}
      <ACFilterBar
        filters={{ ...query.filters, search: query.searchInput }}
        onChange={query.setFilters}
        onReset={query.reset}
        buildingList={buildingList}
      />

      {/* Reactive Asset Table */}
      <ACTable
        acList={acs}
        loading={loading}
        pagination={pagination}
        onPageChange={query.setPage}
        onPageSizeChange={query.setPageSize}
        sort={query.sort || 'ac_id'}
        onSortChange={query.setSort}
        onDeactivate={async ac => {
          const ok = window.confirm(`Decommission ${ac.id}?\n\nThe asset stays in history but is removed from active lists.`);
          if (!ok) return;
          try {
            await decommissionAC(ac.acId ?? ac.id);
          } catch (err) {
            window.alert(err instanceof Error ? err.message : 'Decommission failed.');
          }
        }}
        onInlineSave={handleInlineSave}
      />

      <AssetFormModal
        isOpen={showAddModal}
        mode="create"
        onClose={() => setShowAddModal(false)}
        onSaved={refreshData}
      />
    </div>
  );
}
