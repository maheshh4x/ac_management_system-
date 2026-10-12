'use client';

import React, { useState } from 'react';
import { useACDataset } from '@/hooks/useACDataset';
import { useAssetQueryState } from '@/hooks/useAssetQueryState';
import { ACStatCards } from '@/components/features/ACStatCards';
import { STATUSES } from '@/components/features/ACFilterBar';
import { MaintenanceTrendChart } from '@/components/features/charts/MaintenanceTrendChart';
import { useMaintenanceTrend } from '@/lib/api/maintenance';
import { ImportACModal } from '@/components/features/csv-import/ImportACModal';
import { ACTable } from '@/components/features/ACTable';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { Upload } from 'lucide-react';
import { inlineDraftToPayload } from '@/lib/adapters/asset';
import { AnalyticsDashboard } from '@/components/features/analytics/AnalyticsDashboard';

export default function AdminDashboardPage() {
  const query = useAssetQueryState();
  const [showImportModal, setShowImportModal] = useState(false);

  const {
    acs,
    stats,
    loading,
    error,
    refreshData,
    pagination,
    updateACAsset,
    decommissionAC,
  } = useACDataset(query.filters, {
    page: query.page,
    pageSize: query.pageSize,
    sort: query.sort || 'ac_id',
  });

  // Monthly job counts from SQL (GET /reports/maintenance-trend, VIEW_REPORTS).
  // The chart previously fell back to a hardcoded DEFAULT_TREND_DATA array.
  // The trend receives the SAME selected building as every other
  // visualization (§9): All Buildings sends no block filter.
  const selectedBuilding = query.filters.buildingId;
  const trendQuery = useMaintenanceTrend(
    selectedBuilding && selectedBuilding !== 'All' ? { block: selectedBuilding } : {},
  );
  const trendData = (trendQuery.data?.monthlyTrend ?? []).map(row => ({
    month: row.month,
    jobs: row.total,
  }));

  const handleStatClick = (statusFilter: string) => {
    // Cards double as drill-downs only for real statuses; KPI card ids
    // (Capacity/Depts/Locations/Completeness) would otherwise become a
    // literal `status=<id>` query that can never match a row.
    if (!STATUSES.includes(statusFilter)) return;
    query.setFilters({ status: statusFilter === query.filters.status ? 'All' : statusFilter });
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            NITTTR AC Asset Management Command Center
          </h1>
          <p className="text-slate-500 text-sm mt-0.5">
            Real-time monitoring, maintenance tracking, and asset analytics across campus.
          </p>
        </div>

        <ProtectedAction permission="CREATE_AC">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowImportModal(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs font-semibold shadow-2xs transition-colors"
            >
              <Upload size={15} /> Import College Data
            </button>
          </div>
        </ProtectedAction>
      </div>

      {/* KPI Stat Cards (with status drill-down) */}
      <ACStatCards
        stats={stats}
        loading={loading}
        error={error}
        onStatClick={handleStatClick}
      />

      {/* Filter-driven analytics: global filters + KPIs + visualizations +
          explorer + insights (the filter bar drives the table below too) */}
      <AnalyticsDashboard
        filters={query.filters}
        searchInput={query.searchInput}
        onFiltersChange={query.setFilters}
        onResetFilters={query.reset}
        kpiMode="extended"
      />

      {/* Interactive Charts Section Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Maintenance Trend Line Chart */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-2xs flex flex-col justify-between md:col-span-2 lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-bold text-slate-900">Maintenance Jobs Trend</h3>
              <p className="text-xs text-slate-500">Monthly job counts reported by the maintenance-trend endpoint</p>
            </div>
            <span className="text-[11px] font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">Monthly</span>
          </div>
          <MaintenanceTrendChart
            data={trendData}
            loading={trendQuery.isPending}
            error={trendQuery.error?.message ?? null}
            onRetry={() => void trendQuery.refetch()}
          />
        </div>
      </div>

      {/* Filtered AC Assets Table */}
      <div className="space-y-4">
        <div className="flex items-center justify-between px-1">
          <h2 className="text-lg font-bold text-slate-900">Active AC Asset Records</h2>
          <span className="text-xs font-medium text-slate-500 bg-white px-3 py-1 rounded-full border border-slate-200 shadow-2xs">
            <strong className="text-slate-900">{pagination?.total ?? acs.length}</strong> filtered units
          </span>
        </div>
        <ACTable
          acList={acs}
          loading={loading}
          pagination={pagination}
          onPageChange={query.setPage}
          onPageSizeChange={query.setPageSize}
          sort={query.sort || 'ac_id'}
          onSortChange={query.setSort}
          onInlineSave={async (ac, draft) => {
            await updateACAsset(ac.acId ?? ac.id, inlineDraftToPayload(draft));
          }}
          onDeactivate={async ac => {
            const ok = window.confirm(`Decommission ${ac.id}?`);
            if (ok) {
              try {
                await decommissionAC(ac.acId ?? ac.id);
              } catch (err) {
                window.alert(err instanceof Error ? err.message : 'Decommission failed.');
              }
            }
          }}
        />
      </div>

      {/* Bulk import modal */}
      <ImportACModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        onImported={refreshData}
      />
    </div>
  );
}
