"use client";

// src/hooks/useACDataset.ts
/**
 * Phase 3: the dataset hook now reads from the Flask API instead of the
 * localStorage store. One call fans out to:
 *   - GET /api/v1/assets           (the table page; or every matching row
 *                                   when `options.all` — reports/valuations)
 *   - GET /api/v1/dashboard/summary (filter-aware stats + distributions,
 *                                   aggregated DB-side per Plan #35)
 * Mutations write through /api/v1/assets/<ac_id>.
 */
import { useCallback } from "react";

import {
  assetKeys,
  buildingDistributionFrom,
  capacityDistributionFrom,
  brandDistributionFrom,
  getAssetValueByBuilding,
  getAssetValueByType,
  getAverageACAge,
  getConnectedPowerLoad,
  getFaultFrequencyByBuilding,
  getTotalAssetValue,
  statisticsFrom,
  statusDistributionFrom,
  typeDistributionFrom,
  useAllAssets,
  useDashboardSummary,
  useDecommissionAsset,
  useUpdateAsset,
  useAssets,
  type AssetQuery,
} from "@/lib/api/assets";
import type { ACFilters, ACStatistics, ExtendedAC } from "@/lib/types/asset";
import { useQueryClient } from "@tanstack/react-query";

export interface DatasetOptions {
  page?: number;
  pageSize?: number;
  sort?: string;
  /** Reports/valuations need the full filtered slice, not one page. */
  all?: boolean;
}

/**
 * The UI's default sentinel — "All" (and blank) means "no filter" and must
 * never reach the API: the backend treats any value it receives as a literal
 * predicate (`operational_status ILIKE 'All'` / `block_name ILIKE '%All%'`
 * match nothing), so a default "All" dashboard queried as if the database
 * were empty. Mirrors `useAssetQueryState.clean()` for every caller of this
 * hook, including those that don't go through the URL state.
 */
export function cleanFilter(value?: string | null): string | undefined {
  return value && value !== "All" ? value : undefined;
}

/** UI filter state -> the backend's GET /assets query contract. */
export function toAssetQuery(filters?: ACFilters, options: DatasetOptions = {}): AssetQuery {
  return {
    page: options.page,
    pageSize: options.pageSize,
    sort: options.sort,
    search: cleanFilter(filters?.search),
    status: cleanFilter(filters?.status),
    block: cleanFilter(filters?.buildingId),
    college: cleanFilter(filters?.collegeId),
    department: cleanFilter(filters?.departmentId),
    assetType: cleanFilter(filters?.acType),
    manufacturer: cleanFilter(filters?.manufacturer),
    capacityTon: cleanFilter(filters?.capacityTon),
    floor: cleanFilter(filters?.floorId),
    installedFrom: cleanFilter(filters?.yearFrom),
    installedTo: cleanFilter(filters?.yearTo),
    dueBefore: filters?.dateRange?.end || undefined,
  };
}

export function useACDataset(filters?: ACFilters, options: DatasetOptions = {}) {
  const query = toAssetQuery(filters, options);
  const allMode = options.all === true;

  // Stats/distributions describe the filter slice — never the current page.
  const sliceQuery = toAssetQuery(filters);
  const listQuery = useAssets(query, !allMode);
  const allQuery = useAllAssets(sliceQuery, allMode);
  const summaryQuery = useDashboardSummary(sliceQuery);
  const qc = useQueryClient();

  const acs: ExtendedAC[] = allMode ? (allQuery.data ?? []) : (listQuery.data?.items ?? []);
  const rowsForValuation: ExtendedAC[] = allMode ? (allQuery.data ?? []) : acs;

  const summary = summaryQuery.data;
  const stats: ACStatistics | undefined = statisticsFrom(summary);
  const loading = (allMode ? allQuery.isLoading : listQuery.isPending) || summaryQuery.isPending;
  const error =
    (allMode ? allQuery.error : listQuery.error)?.message ??
    summaryQuery.error?.message ??
    null;

  const refreshData = useCallback(() => {
    void qc.invalidateQueries({ queryKey: assetKeys.all });
  }, [qc]);

  /** PATCH /api/v1/assets/<ac_id> (payload is backend field names). */
  const updateAsset = useUpdateAsset();
  const updateACAsset = useCallback(
    async (acId: string, payload: Record<string, unknown>) => {
      const result = await updateAsset.mutateAsync({ acId, payload });
      return result;
    },
    [updateAsset],
  );

  const decommission = useDecommissionAsset();
  const decommissionAC = useCallback(
    (acId: string) => decommission.mutateAsync(acId),
    [decommission],
  );

  return {
    acs,
    pagination: listQuery.data
      ? {
          page: listQuery.data.pagination.page,
          pageSize: listQuery.data.pagination.page_size,
          total: listQuery.data.pagination.total,
          totalPages: listQuery.data.pagination.total_pages,
        }
      : undefined,
    stats,
    loading,
    error,
    refreshData,
    updateACAsset,
    decommissionAC,
    statusDistribution: statusDistributionFrom(summary),
    typeDistribution: typeDistributionFrom(summary),
    buildingDistribution: buildingDistributionFrom(summary),
    capacityDistribution: capacityDistributionFrom(summary),
    brandDistribution: brandDistributionFrom(summary),
    totalAssetValue: getTotalAssetValue(rowsForValuation),
    avgAcAge: getAverageACAge(rowsForValuation),
    connectedPowerLoad: getConnectedPowerLoad(rowsForValuation),
    assetValueByBuilding: getAssetValueByBuilding(rowsForValuation),
    assetValueByType: getAssetValueByType(rowsForValuation),
    faultFrequencyByBuilding: getFaultFrequencyByBuilding(rowsForValuation),
  };
}
