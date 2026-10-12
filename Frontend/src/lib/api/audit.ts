"use client";

/**
 * Audit trail hooks (Phase 7, FE task 3): `GET /api/v1/audit`.
 *
 * Server-side filters — nothing is filtered in the browser. Supported query
 * params (see app/api/v1/system.py::list_audit):
 *   entity_type  exact, case-insensitive
 *   user         actor profile UUID (400 INVALID_FILTER otherwise)
 *   action       case-insensitive contains
 *   q            action / entity id / actor name contains
 *   from, to     YYYY-MM-DD inclusive on created_at (400 INVALID_DATE)
 *   page, page_size   offset paging (cursor keyset also exists server-side
 *                     but the UI shows "page X of Y", so it uses offsets)
 *
 * `?format=csv` returns a UTF-8 attachment of the same filtered rows and
 * additionally requires VIEW_REPORTS (403 otherwise).
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiBlob, apiList } from "./client";

export interface AuditLogRow {
  id: string;
  user_id: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  /** Already redacted server-side: password-like keys become ***REDACTED***. */
  old_values: Record<string, unknown> | string | null;
  new_values: Record<string, unknown> | string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  actor_name: string | null;
  actor_role: string | null;
}

export interface AuditFilters {
  q?: string;
  entityType?: string;
  action?: string;
  /** Actor profile UUID. */
  actor?: string;
  from?: string;
  to?: string;
  page?: number;
  pageSize?: number;
}

export const auditKeys = {
  list: (filters: AuditFilters) => ["audit", "list", filters] as const,
};

export function auditQueryString(filters: AuditFilters, format?: "csv"): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.entityType) params.set("entity_type", filters.entityType);
  if (filters.action) params.set("action", filters.action);
  if (filters.actor) params.set("user", filters.actor);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (format) params.set("format", format);
  else {
    if (filters.page) params.set("page", String(filters.page));
    if (filters.pageSize) params.set("page_size", String(filters.pageSize));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function useAuditLogs(filters: AuditFilters) {
  return useQuery({
    queryKey: auditKeys.list(filters),
    queryFn: () => apiList<AuditLogRow>(`/api/v1/audit${auditQueryString(filters)}`),
    placeholderData: keepPreviousData, // smooth page/filter transitions
  });
}

/** Download the current filter set as a CSV attachment. */
export function fetchAuditCsv(filters: AuditFilters): Promise<Blob> {
  return apiBlob(`/api/v1/audit${auditQueryString(filters, "csv")}`);
}
