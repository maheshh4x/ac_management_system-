"use client";

/**
 * Movement request hooks (Phase 5, FE task 1).
 *
 * Backed 1:1 by the Phase-5 endpoints:
 *   GET  /api/v1/movements                 list + filters (useMovements)
 *   POST /api/v1/movements                 request a move (useCreateMovement)
 *   POST /api/v1/movements/<id>/approve    decide (useMovementAction)
 *   POST /api/v1/movements/<id>/reject     decide — { rejection_reason }
 *   POST /api/v1/movements/<id>/complete   atomically moves the asset
 *   POST /api/v1/movements/<id>/cancel     requester, PENDING only
 *   POST /api/v1/movements/<id>/comments   thread on a PENDING request
 *
 * The server owns every rule (self-approve 403, one open movement per
 * asset 409 MOVEMENT_OPEN, double-complete 409 INVALID_STATE, manager
 * block scoping …). Mutations are online-only — there is no movement
 * replay endpoint — so conflicts surface via `movementErrorMessage()` and
 * the component refreshes the list (`refetch()` on 409).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mapMovement, type ApiMovement } from "@/lib/adapters/movement";
import type { MovementRequest } from "@/lib/types/movement";
import { assetKeys } from "./assets";
import { api, apiList, ApiError, type Paginated } from "./client";

export const movementKeys = {
  all: ["movements"] as const,
  list: (filters: MovementFilters) => ["movements", "list", filters] as const,
};

/** Mirrors the GET /api/v1/movements query contract (openapi.yaml). */
export interface MovementFilters {
  /** `mine=1` — only requests authored by the session user. */
  mine?: boolean;
  acId?: string;
  /** Comma list of server statuses, e.g. "PENDING" or "PENDING,APPROVED". */
  status?: string;
  /** Requested-at window (ISO dates). */
  from?: string;
  to?: string;
  page?: number;
  pageSize?: number;
}

function toSearch(filters: MovementFilters): string {
  const params = new URLSearchParams();
  if (filters.mine) params.set("mine", "1");
  if (filters.acId) params.set("ac_id", filters.acId);
  if (filters.status) params.set("status", filters.status);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.page) params.set("page", String(filters.page));
  if (filters.pageSize) params.set("page_size", String(filters.pageSize));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export interface MovementsResult {
  movements: MovementRequest[];
  pagination: Paginated<MovementRequest>["pagination"] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/** Enriched movement list, server-filtered, mapped into the UI vocabulary. */
export function useMovements(filters: MovementFilters = {}, enabled = true): MovementsResult {
  const query = useQuery({
    queryKey: movementKeys.list(filters),
    enabled,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const page = await apiList<ApiMovement>(`/api/v1/movements${toSearch(filters)}`);
      return {
        movements: page.items.map(mapMovement),
        pagination: page.pagination,
      };
    },
  });
  return {
    movements: query.data?.movements ?? [],
    pagination: query.data?.pagination,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  };
}

export interface CreateMovementInput {
  /** Business key (AC-…) of the asset to move. */
  acId: string;
  /** Destination room — must differ from the asset's current room. */
  toRoomId: string;
  reason: string;
}

/** Request a relocation (any role with CREATE_MOVEMENT). */
export function useCreateMovement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateMovementInput) =>
      api<{ id: string; status: string }>("/api/v1/movements", {
        method: "POST",
        body: {
          ac_id: input.acId,
          to_room_id: input.toRoomId,
          reason: input.reason,
        },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: movementKeys.all }),
  });
}

export type MovementAction = "approve" | "reject" | "complete" | "cancel";

export interface MovementActionInput {
  id: string;
  action: MovementAction;
  /** reject → { rejection_reason: string } (server: 400 REASON_REQUIRED). */
  payload?: Record<string, unknown>;
}

/** 409 = the list is stale (decided/double-completed elsewhere): refresh. */
export function isMovementConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/** Human message per server error code (unknown codes pass through). */
export function movementErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "The movement request failed.";
  switch (error.code) {
    case "MOVEMENT_OPEN":
      return "This asset already has an open movement request — the list was refreshed; resolve it first.";
    case "INVALID_STATE":
      return "This request changed on the server — the list was refreshed, please try again.";
    case "SELF_APPROVE":
      return "You cannot decide your own movement request.";
    case "FORBIDDEN":
      return "You can only decide movements for the blocks you manage.";
    case "REASON_REQUIRED":
      return "A rejection reason is required.";
    case "SAME_ROOM":
      return "The asset is already in that room.";
    case "ASSET_DECOMMISSIONED":
      return "Decommissioned assets cannot be moved.";
    case "ASSET_NOT_FOUND":
      return "The asset no longer exists.";
    case "NOT_FOUND":
      return "This movement request no longer exists — the list was refreshed.";
    default:
      return error.message || "The movement request failed.";
  }
}

/**
 * Approve / reject / complete / cancel. Completing changes `acs.room_id`
 * server-side, so the asset list + detail caches are invalidated too.
 */
export function useMovementAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, payload }: MovementActionInput) =>
      api<{ id: string; status: string }>(
        `/api/v1/movements/${encodeURIComponent(id)}/${action}`,
        { method: "POST", body: payload ?? {} },
      ),
    onSuccess: (_data, input) => {
      void qc.invalidateQueries({ queryKey: movementKeys.all });
      // Room embedded in asset rows changed on completion.
      if (input.action === "complete") void qc.invalidateQueries({ queryKey: assetKeys.all });
    },
  });
}

/** Free-text note on a PENDING request (POST /movements/<id>/comments). */
export function useAddMovementComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) =>
      api<Record<string, unknown>>(`/api/v1/movements/${encodeURIComponent(id)}/comments`, {
        method: "POST",
        body: { body },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: movementKeys.all }),
  });
}
