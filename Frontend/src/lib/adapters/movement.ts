/**
 * Movement adapter (Phase 2, task 7; extended Phase 5). Backend uses
 * upper-case statuses; UI uses Title Case (MovementRequest). The list
 * endpoint enriches rows with composed "Block / Floor / Room" locations,
 * requester/approver names and the movement_comments thread.
 */
import { z } from "zod";
import type { MovementComment, MovementRequest } from "@/lib/types/movement";

const apiMovementCommentSchema = z
  .object({
    id: z.string(),
    author_id: z.string().nullish(),
    author_name: z.string().nullish(),
    body: z.string().nullish(),
    created_at: z.string().nullish(),
  })
  .loose();

export const apiMovementSchema = z
  .object({
    id: z.string(),
    ac_id: z.string().nullish(),
    asset_uuid: z.string().nullish(),
    asset_code: z.string().nullish(),
    make: z.string().nullish(),
    model: z.string().nullish(),
    from_room_id: z.string().nullish(),
    to_room_id: z.string().nullish(),
    from_room_name: z.string().nullish(),
    from_floor_name: z.string().nullish(),
    from_block_name: z.string().nullish(),
    to_room_name: z.string().nullish(),
    to_floor_name: z.string().nullish(),
    to_block_name: z.string().nullish(),
    from_location: z.string().nullish(),
    to_location: z.string().nullish(),
    requested_by: z.string().nullish(),
    requested_by_name: z.string().nullish(),
    requested_by_email: z.string().nullish(),
    requested_by_role: z.string().nullish(),
    approved_by: z.string().nullish(),
    approved_by_name: z.string().nullish(),
    completed_by: z.string().nullish(),
    completed_by_name: z.string().nullish(),
    requested_at: z.string().nullish(),
    approved_at: z.string().nullish(),
    completed_at: z.string().nullish(),
    cancelled_at: z.string().nullish(),
    reason: z.string().nullish(),
    rejection_reason: z.string().nullish(),
    status: z.string(),
    comments: z.array(apiMovementCommentSchema).nullish(),
  })
  .loose();

export type ApiMovement = z.infer<typeof apiMovementSchema>;

type UiMovementStatus = MovementRequest["status"];

export function toUiMovementStatus(status: string): UiMovementStatus {
  switch (status.trim().toUpperCase()) {
    case "APPROVED":
      return "Approved";
    case "REJECTED":
      return "Rejected";
    case "COMPLETED":
      return "Completed";
    case "CANCELLED":
      return "Cancelled";
    default:
      return "Pending";
  }
}

/** Composed "Block / Floor / Room", falling back to parts then raw ids. */
function composeLocation(m: ApiMovement, side: "from" | "to"): string {
  const composed = side === "from" ? m.from_location : m.to_location;
  if (composed) return composed;
  const joined = [
    side === "from" ? m.from_block_name : m.to_block_name,
    side === "from" ? m.from_floor_name : m.to_floor_name,
    side === "from" ? m.from_room_name : m.to_room_name,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" / ");
  if (joined) return joined;
  return (side === "from" ? m.from_room_id : m.to_room_id) ?? "";
}

function mapComments(raw: ApiMovement["comments"]): MovementComment[] {
  return (raw ?? []).map(comment => ({
    id: comment.id,
    author: comment.author_name ?? comment.author_id ?? "Unknown",
    message: comment.body ?? "",
    createdAt: comment.created_at ?? "",
  }));
}

export function mapMovement(row: ApiMovement): MovementRequest {
  const m = apiMovementSchema.parse(row);
  return {
    id: m.id,
    acId: m.ac_id ?? "",
    fromLocation: composeLocation(m, "from"),
    toLocation: composeLocation(m, "to"),
    reason: m.reason ?? "",
    requestedBy: m.requested_by_name ?? m.requested_by ?? "",
    requestedById: m.requested_by ?? undefined,
    requestedByEmail: m.requested_by_email ?? undefined,
    requestedByRole: m.requested_by_role ?? "",
    status: toUiMovementStatus(m.status),
    requestDate: m.requested_at ?? "",
    comments: mapComments(m.comments),
    approvedBy: m.approved_by_name ?? m.approved_by ?? undefined,
    approvedDate: m.approved_at ?? undefined,
    rejectionReason: m.rejection_reason ?? undefined,
    completedBy: m.completed_by_name ?? m.completed_by ?? undefined,
    completedDate: m.completed_at ?? undefined,
    cancelledDate: m.cancelled_at ?? undefined,
    assetCode: m.asset_code ?? undefined,
    assetMake: m.make ?? undefined,
    assetModel: m.model ?? undefined,
  };
}
