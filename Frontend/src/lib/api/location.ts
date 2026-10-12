"use client";

/**
 * Location architecture hooks (permanent location model, §6.1/§12/§13):
 * the review queue, alias CRUD and batch re-resolution against
 * /api/v1/campus/location/*, plus the per-level manual confirmation PATCH
 * against /api/v1/assets/<ref>/location.
 *
 * Query-key factory keeps everything under ["location", ...] so one
 * invalidation refreshes queue + aliases together; row mutations that can
 * finish pending levels (re-resolve, manual confirm) also invalidate the
 * asset keys, since detail/list rows carry the nested location payload.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mapAsset, type ApiAsset } from "@/lib/adapters/asset";
import type { LocationLevelKey } from "@/lib/types/asset";
import { api, apiList, type Paginated } from "./client";
import { assetKeys } from "./assets";

/** One alias row (location_aliases, §6.1). Soft-deleted = is_active false. */
export interface LocationAlias {
  id: number;
  entity_type: LocationLevelKey;
  entity_id: string;
  alias: string;
  normalized_alias: string;
  source: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
  /** Review rows at this level whose unmatched source normalizes exactly to
      this alias — the rows a re-resolve will finish (count only; the create
      endpoint never re-resolves implicitly). */
  matching_review_rows?: number;
}

export interface LocationReviewFilters {
  page?: number;
  pageSize?: number;
  level?: LocationLevelKey | null;
}

export interface LocationReviewPage {
  items: ExtendedAsset[];
  pagination: Paginated<ApiAsset>["pagination"];
}

/** Asset rows carry the nested location payload (mapped by mapAsset). */
type ExtendedAsset = ReturnType<typeof mapAsset>;

export interface ReResolveResult {
  scanned: number;
  updated: number;
}

export const locationKeys = {
  all: ["location"] as const,
  review: (page: number, level: string | null) => ["location", "review", page, level] as const,
  aliases: (includeInactive: boolean) => ["location", "aliases", includeInactive] as const,
};

/** §13 review queue: source text with no confirmed id, newest page slice. */
export function useLocationReview(filters: LocationReviewFilters = {}, enabled = true) {
  const page = filters.page ?? 1;
  const pageSize = filters.pageSize ?? 25;
  const level = filters.level ?? null;
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (level) params.set("level", level);
  return useQuery({
    queryKey: locationKeys.review(page, level),
    enabled,
    queryFn: async (): Promise<LocationReviewPage> => {
      const raw = await apiList<ApiAsset>(`/api/v1/campus/location/review?${params.toString()}`);
      return { items: raw.items.map(mapAsset), pagination: raw.pagination };
    },
  });
}

/** Alias directory — active rows only unless the toggle asks for history. */
export function useLocationAliases(includeInactive = false, enabled = true) {
  const suffix = includeInactive ? "?include_inactive=true" : "";
  return useQuery({
    queryKey: locationKeys.aliases(includeInactive),
    enabled,
    queryFn: async () =>
      (await api<{ aliases: LocationAlias[] }>(`/api/v1/campus/location/aliases${suffix}`)).aliases,
  });
}

/**
 * Create (or idempotently reactivate) one alias. An active alias pointing at
 * a different entity answers 409 ALIAS_EXISTS — the UI surfaces that error
 * rather than repointing (never silently rewrite a confirmed mapping).
 */
export function useCreateLocationAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { entity_type: LocationLevelKey; entity_id: string; alias: string }) =>
      api<LocationAlias>("/api/v1/campus/location/aliases", { method: "POST", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: locationKeys.all }),
  });
}

/** Soft delete — the alias stops resolving immediately, history is kept. */
export function useDeleteLocationAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (aliasId: number) =>
      api<{ id: number; deleted: boolean }>(`/api/v1/campus/location/aliases/${aliasId}`, {
        method: "DELETE",
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: locationKeys.all }),
  });
}

/**
 * Batch re-resolution: one pass over stored source values (idempotent, never
 * touches manual pins). Finishing levels changes asset rows, so both caches
 * go.
 */
export function useReResolveLocations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (limit: number) =>
      api<ReResolveResult>("/api/v1/campus/location/re-resolve", {
        method: "POST",
        body: { limit },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: locationKeys.all });
      void qc.invalidateQueries({ queryKey: assetKeys.all });
    },
  });
}

/**
 * Per-level manual confirmation (§10): an id pins the level, an explicit
 * null clears it, omitted keys stay untouched. Rejects contradicting
 * chains (400 INVALID_LOCATION_CHAIN) — the UI shows the error verbatim.
 */
export function usePatchAssetLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      ref,
      levels,
      note,
    }: {
      ref: string;
      levels: Partial<Record<LocationLevelKey, string | null>>;
      note?: string;
    }) =>
      api<{ ac_id: string; location: unknown }>(`/api/v1/assets/${encodeURIComponent(ref)}/location`, {
        method: "PATCH",
        body: { ...levels, ...(note ? { note } : {}) },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: locationKeys.all });
      void qc.invalidateQueries({ queryKey: assetKeys.all });
    },
  });
}
