'use client';

import React from 'react';
import { useAssetQueryState } from '@/hooks/useAssetQueryState';
import { AnalyticsDashboard } from '@/components/features/analytics/AnalyticsDashboard';

export default function ViewerDashboardPage() {
  const query = useAssetQueryState();

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Observer Dashboard</h1>
        <p className="text-text-secondary text-sm">
          Read-only view of AC assets and campus infrastructure.
        </p>
      </div>

      {/* Filter-driven inventory analytics (read-only; viewers share the
          same component as admins — RBAC stays page-level + server-side). */}
      <AnalyticsDashboard
        filters={query.filters}
        searchInput={query.searchInput}
        onFiltersChange={query.setFilters}
        onResetFilters={query.reset}
        kpiMode="full"
      />
    </div>
  );
}
