'use client';

import React from 'react';
import { useACDataset } from '@/hooks/useACDataset';
import { useAssetQueryState } from '@/hooks/useAssetQueryState';
import { useAssetFacets } from '@/lib/api/assets';
import { ACFilterBar } from '@/components/features/ACFilterBar';
import { ACTable } from '@/components/features/ACTable';
import { AlertTriangle } from 'lucide-react';

export default function ViewerAssetsPage() {
  // Filters, page, size and sort all live in the URL query string (same
  // contract as the admin inventory page).
  const query = useAssetQueryState();

  // Read-only: no update/decommission/import affordances are wired, so the
  // table renders detail + map links only (edit controls gate on
  // SUPER_ADMIN / EDIT_AC internally as well).
  const {
    acs,
    stats,
    loading,
    error,
    pagination,
  } = useACDataset(query.filters, {
    page: query.page,
    pageSize: query.pageSize,
    sort: query.sort || 'ac_id',
  });

  const { data: facets } = useAssetFacets();
  const buildingList = (facets?.block ?? []).map(b => ({ id: b.value, name: b.value, count: b.count }));

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AC Asset Inventory</h1>
          <p className="text-sm text-slate-500 mt-0.5">Browse college AC units across campus blocks. Read-only access.</p>
        </div>
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

      {/* Assets Table (no onEdit/onDeactivate/onInlineSave: detail + map links only) */}
      <ACTable
        acList={acs}
        loading={loading}
        pagination={pagination}
        onPageChange={query.setPage}
        onPageSizeChange={query.setPageSize}
        sort={query.sort || 'ac_id'}
        onSortChange={query.setSort}
      />
    </div>
  );
}
