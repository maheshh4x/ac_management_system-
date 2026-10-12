/**
 * Movement domain types (Phase 5) — home of `MovementRequest` after the
 * mock seed in `mock-data.ts` and the localStorage `movement-data-service`
 * were retired. Types only: no store, no side effects; data comes from
 * `src/lib/api/movements.ts`.
 */

export type MovementStatus = "Pending" | "Approved" | "Rejected" | "Completed" | "Cancelled";

export interface MovementComment {
  id: string;
  /** Display name of the author (profile name, else email). */
  author: string;
  message: string;
  createdAt: string;
}

export interface MovementRequest {
  id: string;
  acId: string;
  fromLocation: string;
  toLocation: string;
  reason: string;
  /** Requester display name. */
  requestedBy: string;
  requestedById?: string;
  requestedByEmail?: string;
  requestedByRole: string;
  status: MovementStatus;
  requestDate: string;
  /** movement_comments thread — always present (empty while PENDING). */
  comments: MovementComment[];
  approvedBy?: string;
  approvedDate?: string;
  rejectionReason?: string;
  completedBy?: string;
  completedDate?: string;
  cancelledDate?: string;
  /** Enriched list join (asset context). */
  assetCode?: string;
  assetMake?: string;
  assetModel?: string;
}
