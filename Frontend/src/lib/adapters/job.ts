/**
 * Maintenance job adapter (Phase 2, task 7).
 * Backend work orders use UPPER_SNAKE status/priority and SCHEDULED/ASSIGNED
 * lifecycle states; the UI uses Title Case with Pending/Accepted. The full
 * lifecycle endpoints (accept/start/complete/escalate) land in Phase 4 — this
 * adapter is the stable vocabulary contract both sides code against.
 */
import { z } from "zod";
import type { MaintenanceJob } from "@/lib/types/maintenance";

export const apiWorkOrderSchema = z
  .object({
    id: z.string(),
    ac_id: z.string().nullish(),
    asset_uuid: z.string().nullish(),
    maintenance_type: z.string().nullish(),
    priority: z.string().nullish(),
    status: z.string(),
    scheduled_date: z.string().nullish(),
    assigned_at: z.string().nullish(),
    accepted_at: z.string().nullish(),
    started_at: z.string().nullish(),
    completed_at: z.string().nullish(),
    escalated_at: z.string().nullish(),
    escalation_reason: z.string().nullish(),
    technician_id: z.string().nullish(),
    technician_name: z.string().nullish(),
    problem_description: z.string().nullish(),
    work_performed: z.string().nullish(),
    parts_used: z.string().nullish(),
    labor_hours: z.coerce.number().nullish(),
    remarks: z.string().nullish(),
    created_by: z.string().nullish(),
    created_by_name: z.string().nullish(),
    created_at: z.string().nullish(),
    updated_at: z.string().nullish(),
    room_name: z.string().nullish(),
    block_name: z.string().nullish(),
    floor_name: z.string().nullish(),
  })
  .loose();

export type ApiWorkOrder = z.infer<typeof apiWorkOrderSchema>;

type UiJobStatus = MaintenanceJob["status"];
type UiJobType = MaintenanceJob["type"];
type UiJobPriority = MaintenanceJob["priority"];

/** API-side vocabulary (kept local so this adapter stays dependency-free —
 *  `lib/api/maintenance.ts` imports *from* this module). */
export type ApiPriority = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type ApiStatusFilter =
  | "SCHEDULED"
  | "ASSIGNED"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "CANCELLED";

export function toUiJobStatus(status: string): UiJobStatus {
  switch (status.trim().toUpperCase()) {
    case "SCHEDULED":
      return "Pending";
    case "ASSIGNED":
      return "Accepted";
    case "IN_PROGRESS":
      return "In Progress";
    case "COMPLETED":
      return "Completed";
    case "CANCELLED":
      return "Cancelled";
    default:
      return "Pending";
  }
}

export function toUiJobPriority(priority: string | null | undefined): UiJobPriority {
  switch ((priority ?? "").trim().toUpperCase()) {
    case "LOW":
      return "Low";
    case "HIGH":
      return "High";
    case "CRITICAL":
      return "Critical";
    default:
      return "Medium";
  }
}

export function toUiJobType(type: string | null | undefined): UiJobType {
  switch ((type ?? "").trim().toLowerCase()) {
    case "preventive":
      return "Preventive";
    case "emergency":
      return "Emergency";
    case "inspection":
      return "Inspection";
    default:
      return "Repair";
  }
}

/** UI status → the API status filter value (`GET /maintenance?status=`). */
export function toApiJobStatus(status: UiJobStatus): ApiStatusFilter {
  switch (status) {
    case "Pending":
      return "SCHEDULED";
    case "Accepted":
      return "ASSIGNED";
    case "In Progress":
      return "IN_PROGRESS";
    case "Completed":
      return "COMPLETED";
    case "Cancelled":
      return "CANCELLED";
  }
}

/** UI priority → the API priority value accepted by create/patch/filters. */
export function toApiJobPriority(priority: UiJobPriority): ApiPriority {
  switch (priority) {
    case "Low":
      return "LOW";
    case "High":
      return "HIGH";
    case "Critical":
      return "CRITICAL";
    default:
      return "MEDIUM";
  }
}

export function mapWorkOrder(row: ApiWorkOrder): MaintenanceJob {
  const o = apiWorkOrderSchema.parse(row);
  const location = [o.block_name, o.floor_name, o.room_name].filter(Boolean).join(" / ");
  return {
    id: o.id,
    acId: o.ac_id ?? "",
    acLocation: location || "—",
    type: toUiJobType(o.maintenance_type),
    status: toUiJobStatus(o.status),
    priority: toUiJobPriority(o.priority),
    assignedTo: o.technician_name ?? "",
    assignedToId: o.technician_id ?? undefined,
    assignedAt: o.assigned_at ?? undefined,
    acceptedAt: o.accepted_at ?? undefined,
    reportedBy: o.created_by_name ?? o.created_by ?? "",
    description: o.problem_description ?? "",
    scheduledDate: o.scheduled_date ?? "",
    completedDate: o.completed_at ?? undefined,
    notes: o.remarks ?? undefined,
    workPerformed: o.work_performed ?? undefined,
    escalatedAt: o.escalated_at ?? undefined,
    escalationReason: o.escalation_reason ?? undefined,
  };
}
