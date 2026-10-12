/**
 * User/profile adapter (Phase 2, task 7). Backend profile rows use
 * snake_case columns and upper-case status; the AuthContext User keeps the
 * established frontend shape (camelCase, lower-case status union).
 */
import { z } from "zod";
import type { Role } from "@/lib/permissions";
import type { User } from "@/contexts/AuthContext";

export const ROLE_SCHEMA = z.enum(["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"]);

/**
 * Phase 7: `ApprovalStatus` / `ManagedUser` used to live in `lib/user-service.ts`,
 * a localStorage mock store retired this phase. The shape itself is real — it
 * is what `mapProfileToManagedUser` produces and what `ApprovalQueue` renders —
 * so only the store was deleted, not the type.
 */
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ManagedUser {
  id: string;
  name: string;
  email: string;
  role: Role;
  department: string;
  phone: string;
  status: ApprovalStatus;
  createdAt: string;
  isActive?: boolean;
  address?: string;
  staffId?: string;
  designation?: string;
  assignedBlocks?: string[];
  yearsOfExperience?: number;
  certificationNumber?: string;
  reportingManagerId?: string;
  requestedAt?: string;
  requestedBy?: string;
  requestedRole?: Role;
  approvedBy?: string;
  approvedAt?: string;
  rejectionReason?: string;
}

export const apiProfileSchema = z
  .object({
    id: z.string(),
    email: z.string().nullish(),
    name: z.string().nullish(),
    role: ROLE_SCHEMA.nullish(),
    status: z.string().nullish(),
    requested_role: z.string().nullish(),
    phone: z.string().nullish(),
    address: z.string().nullish(),
    staff_id: z.string().nullish(),
    designation: z.string().nullish(),
    assigned_blocks: z.array(z.string()).nullish(),
    years_experience: z.coerce.number().nullish(),
    certification_no: z.string().nullish(),
    reporting_manager_id: z.string().nullish(),
    is_active: z.boolean().nullish(),
    approved_by: z.string().nullish(),
    approved_at: z.string().nullish(),
    rejection_reason: z.string().nullish(),
    created_at: z.string().nullish(),
  })
  .loose();

export type ApiProfile = z.infer<typeof apiProfileSchema>;

export type UiStatus = NonNullable<User["status"]>;

export function toUiStatus(status: string | null | undefined): UiStatus {
  switch ((status ?? "").trim().toUpperCase()) {
    case "APPROVED":
      return "approved";
    case "REJECTED":
      return "rejected";
    default:
      return "pending";
  }
}

/** Profile row -> session user (AuthContext). NULL/missing status = approved
 * (pre-migration profiles have no status column). */
export function mapProfileToUser(row: ApiProfile): User {
  const p = apiProfileSchema.parse(row);
  return {
    id: p.id,
    name: p.name ?? "",
    email: p.email ?? "",
    role: p.role ?? "VIEWER",
    phone: p.phone ?? undefined,
    address: p.address ?? undefined,
    staffId: p.staff_id ?? undefined,
    designation: p.designation ?? undefined,
    assignedBlocks: p.assigned_blocks ?? undefined,
    yearsOfExperience: p.years_experience ?? undefined,
    certificationNumber: p.certification_no ?? undefined,
    reportingManagerId: p.reporting_manager_id ?? undefined,
    approvedAt: p.approved_at ?? undefined,
    status: p.status ? toUiStatus(p.status) : "approved",
  };
}

/** Profile row -> admin user-management record. */
export function mapProfileToManagedUser(row: ApiProfile): ManagedUser {
  const p = apiProfileSchema.parse(row);
  const user = mapProfileToUser(p);
  return {
    id: user.id,
    name: user.name,
    email: user.email,
    role: user.role as Role,
    department: "",
    phone: user.phone ?? "",
    status: toUiStatus(p.status),
    createdAt: p.created_at ?? "",
    isActive: p.is_active !== false,
    address: user.address,
    staffId: user.staffId,
    designation: user.designation,
    assignedBlocks: user.assignedBlocks,
    yearsOfExperience: user.yearsOfExperience,
    certificationNumber: user.certificationNumber,
    reportingManagerId: user.reportingManagerId,
    requestedRole: (p.requested_role as Role | null) ?? undefined,
    approvedBy: p.approved_by ?? undefined,
    approvedAt: user.approvedAt,
    rejectionReason: p.rejection_reason ?? undefined,
  };
}

/** Whitelisted self-edit fields -> PATCH /api/v1/me payload. */
export function toProfilePatch(input: Partial<User>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (input.name !== undefined) payload.name = input.name;
  if (input.phone !== undefined) payload.phone = input.phone;
  if (input.address !== undefined) payload.address = input.address;
  if (input.designation !== undefined) payload.designation = input.designation;
  if (input.certificationNumber !== undefined) payload.certification_no = input.certificationNumber;
  if (input.yearsOfExperience !== undefined) payload.years_experience = input.yearsOfExperience;
  return payload;
}
