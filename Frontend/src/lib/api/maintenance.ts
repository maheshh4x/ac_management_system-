"use client";

/**
 * Maintenance / work-order hooks (Phase 4, FE task 1).
 *
 * Backed 1:1 by the Phase-4 endpoints:
 *   GET    /api/v1/maintenance                 list + filters (useJobs)
 *   GET    /api/v1/maintenance/<id>            detail + events + comments (useJob)
 *   POST   /api/v1/maintenance                 create (useCreateJob)
 *   PATCH  /api/v1/maintenance/<id>            content-only edit (API-only — no FE hook)
 *   POST   /api/v1/maintenance/<id>/<action>   assign/accept/start/complete/escalate/cancel
 *   POST   /api/v1/maintenance/sync            offline replay (useSyncNow)
 *   GET    /api/v1/reports/maintenance-trend   charts + stats (useMaintenanceTrend)
 *
 * Job actions taken while offline (or when the network drops mid-request)
 * are queued in IndexedDB (`offline-queue.ts`) with a fresh
 * `client_mutation_id` and replayed through /maintenance/sync — the server
 * dedupes, so replays are idempotent.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, apiList, ApiError, type Paginated } from "./client";
import { assetKeys } from "./assets";
import { reportQueryString, type ReportFilters } from "./reports";
import {
  mergeSyncResults,
  readQueue,
  writeQueue,
  type JobAction,
  type QueuedJobMutation,
  type SyncResultItem,
} from "./offline-queue";
import { mapWorkOrder, type ApiWorkOrder } from "@/lib/adapters/job";
import type { MaintenanceJob } from "@/lib/types/maintenance";

// ── query keys ───────────────────────────────────────────────────────────────

export const maintenanceKeys = {
  all: ["maintenance"] as const,
  list: (filters: JobFilters) => ["maintenance", "list", filters] as const,
  detail: (id: string) => ["maintenance", "detail", id] as const,
  trend: (filters: ReportFilters = {}) => ["maintenance", "trend", filters] as const,
  queue: ["maintenance", "sync-queue"] as const,
};

// ── types ────────────────────────────────────────────────────────────────────

export type ApiJobStatus = "SCHEDULED" | "ASSIGNED" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";
export type ApiJobPriority = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

/** Mirrors the query params of GET /api/v1/maintenance. */
export interface JobFilters {
  /** "me" resolves server-side to the caller's jobs (assigned or created). */
  assignedTo?: string;
  status?: ApiJobStatus;
  priority?: ApiJobPriority;
  /** ISO dates (YYYY-MM-DD) on scheduled_date. */
  from?: string;
  to?: string;
  acId?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}

/** One `maintenance_events` row — the append-only job timeline. */
export interface JobEvent {
  id: string;
  work_order_id: string;
  actor_id: string | null;
  actor_name: string | null;
  event: string;
  from_status: string | null;
  to_status: string | null;
  note: string | null;
  client_mutation_id: string | null;
  created_at: string;
}

export interface JobComment {
  id: string;
  body: string;
  photos: string[];
  author_name: string | null;
  created_at: string;
}

export interface JobDetail {
  job: MaintenanceJob;
  events: JobEvent[];
  comments: JobComment[];
}

/** Response of POST /api/v1/maintenance/sync. */
export interface SyncResponse {
  results: SyncResultItem[];
  applied: number;
  duplicate: number;
  rejected: number;
}

export interface CreateJobInput {
  ac_id: string;
  maintenance_type: string;
  priority?: ApiJobPriority;
  status?: "SCHEDULED" | "ASSIGNED";
  scheduled_date?: string;
  problem_description?: string;
  remarks?: string;
  /** Self-assign on create (technician self-report) or assign to a tech. */
  technician_id?: string;
}

// ── queries ──────────────────────────────────────────────────────────────────

function toSearch(filters: JobFilters): string {
  const params = new URLSearchParams();
  if (filters.assignedTo) params.set("assigned_to", filters.assignedTo);
  if (filters.status) params.set("status", filters.status);
  if (filters.priority) params.set("priority", filters.priority);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.acId) params.set("ac_id", filters.acId);
  if (filters.q) params.set("q", filters.q);
  if (filters.page) params.set("page", String(filters.page));
  if (filters.pageSize) params.set("page_size", String(filters.pageSize));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export interface JobsResult {
  jobs: MaintenanceJob[];
  pagination: Paginated<MaintenanceJob>["pagination"] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/** List of work orders, server-filtered, mapped into the UI vocabulary. */
export function useJobs(filters: JobFilters = {}, enabled = true): JobsResult {
  const query = useQuery({
    queryKey: maintenanceKeys.list(filters),
    enabled,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const page = await apiList<ApiWorkOrder>(`/api/v1/maintenance${toSearch(filters)}`);
      return {
        jobs: page.items.map(mapWorkOrder),
        pagination: page.pagination,
      };
    },
  });
  return {
    jobs: query.data?.jobs ?? [],
    pagination: query.data?.pagination,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  };
}

/** One work order with its `maintenance_events` timeline and comments. */
export function useJob(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: maintenanceKeys.detail(id ?? ""),
    enabled: Boolean(id) && enabled,
    queryFn: async () => {
      const row = await api<Record<string, unknown>>(
        `/api/v1/maintenance/${encodeURIComponent(id as string)}`,
      );
      return {
        job: mapWorkOrder(row as ApiWorkOrder),
        events: (row.events as JobEvent[]) ?? [],
        comments: (row.comments as JobComment[]) ?? [],
      } satisfies JobDetail;
    },
  });
}

/**
 * Charts + KPIs for the reports module (VIEW_REPORTS).
 *
 * Optional `filters` map 1:1 onto the endpoint's `?from&to&block` contract:
 * the trend report buckets `COALESCE(scheduled_date, completed_at::date)` into
 * months inside that window (capped at MAX_TREND_MONTHS server-side).
 */
export function useMaintenanceTrend(filters: ReportFilters = {}) {
  return useQuery({
    queryKey: maintenanceKeys.trend(filters),
    queryFn: () =>
      api<{
        monthlyTrend: Array<{
          month: string;
          fullMonth: string;
          total: number;
          Preventive: number;
          Repair: number;
          Emergency: number;
          Inspection: number;
        }>;
        priorityDistribution: Array<{ name: string; value: number }>;
        stats: {
          total: number;
          scheduled: number;
          assigned: number;
          in_progress: number;
          completed: number;
          cancelled: number;
          mtdCount: number;
          mttrDays: number;
          overdueCount: number;
          upcomingCount: number;
        };
      }>(`/api/v1/reports/maintenance-trend${reportQueryString(filters)}`),
  });
}

// ── mutations ────────────────────────────────────────────────────────────────

/** Schedule a job (managers/admins, or a technician self-report). */
export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateJobInput) =>
      api<Record<string, unknown>>("/api/v1/maintenance", { method: "POST", body: input }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: maintenanceKeys.all }),
  });
}

/** Free-text note on a job (POST /maintenance/<id>/comments, photos ≤ 10). */
export function useAddComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body, photos }: { id: string; body: string; photos?: string[] }) =>
      api<Record<string, unknown>>(`/api/v1/maintenance/${encodeURIComponent(id)}/comments`, {
        method: "POST",
        body: { body, photos: photos ?? [] },
      }),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: maintenanceKeys.detail(id) });
    },
  });
}

export interface JobActionInput {
  id: string;
  action: JobAction;
  payload?: Record<string, unknown>;
  /** Shown in the offline queue panel (e.g. "Accept job"). */
  label?: string;
}

async function enqueue(input: JobActionInput): Promise<QueuedJobMutation> {
  const item: QueuedJobMutation = {
    client_mutation_id: crypto.randomUUID(),
    action: input.action,
    job_id: input.id,
    payload: input.payload ?? {},
    queued_at: Date.now(),
    label: input.label ?? `${input.action} job`,
    state: "pending",
  };
  const queue = await readQueue();
  await writeQueue([...queue, item]);
  return item;
}

/**
 * Run a lifecycle action. If the browser is offline — or the request dies on
 * the wire (NETWORK_ERROR/TIMEOUT) — the mutation is queued for
 * `useSyncNow()` instead of failing. Server-side rejections (403/409/…)
 * surface immediately; the server always decides.
 */
export function useJobAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: JobActionInput): Promise<
      { queued: true; item: QueuedJobMutation } | { queued: false; data: unknown }
    > => {
      const offline = typeof navigator !== "undefined" && navigator.onLine === false;
      if (offline) return { queued: true, item: await enqueue(input) };
      try {
        const data = await api<unknown>(
          `/api/v1/maintenance/${encodeURIComponent(input.id)}/${input.action}`,
          { method: "POST", body: input.payload ?? {} },
        );
        return { queued: false, data };
      } catch (error) {
        const connectionDied =
          error instanceof ApiError && error.status === 0 &&
          (error.code === "NETWORK_ERROR" || error.code === "TIMEOUT");
        if (connectionDied) return { queued: true, item: await enqueue(input) };
        throw error;
      }
    },
    onSuccess: (result, input) => {
      if (result.queued) void qc.invalidateQueries({ queryKey: maintenanceKeys.queue });
      // Server applied it (or queued): the list/detail are stale either way.
      void qc.invalidateQueries({ queryKey: maintenanceKeys.all });
      if (input.action === "complete") void qc.invalidateQueries({ queryKey: assetKeys.all });
    },
  });
}

// ── offline queue ────────────────────────────────────────────────────────────

/** Pending + rejected mutations waiting to be replayed. */
export function useSyncQueue() {
  return useQuery({
    queryKey: maintenanceKeys.queue,
    queryFn: readQueue,
  });
}

/**
 * Replay the queue through POST /maintenance/sync (manual button + `online`
 * event). Rejected items are retried too — a transient failure can heal, a
 * permanent one just re-rejects with its reason.
 */
export function useSyncNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<SyncResponse> => {
      const queue = await readQueue();
      if (!queue.length) return { results: [], applied: 0, duplicate: 0, rejected: 0 };
      const response = await api<SyncResponse>("/api/v1/maintenance/sync", {
        method: "POST",
        body: {
          mutations: queue.map(({ client_mutation_id, action, job_id, payload }) => ({
            client_mutation_id,
            action,
            job_id,
            payload,
          })),
        },
      });
      await writeQueue(mergeSyncResults(queue, response.results ?? []));
      return response;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: maintenanceKeys.queue });
      void qc.invalidateQueries({ queryKey: maintenanceKeys.all });
    },
  });
}

/** Drop one queued item (dismiss a rejection the user won't retry). */
export function useDismissQueued() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (clientMutationId: string) => {
      const queue = await readQueue();
      const next = queue.filter(item => item.client_mutation_id !== clientMutationId);
      await writeQueue(next);
      return next;
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: maintenanceKeys.queue }),
  });
}
