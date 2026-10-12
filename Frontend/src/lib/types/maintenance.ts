/**
 * UI vocabulary for maintenance jobs (Phase 4).
 *
 * The backend speaks UPPER_SNAKE work-order rows (SCHEDULED/ASSIGNED/…);
 * `lib/adapters/job.ts` maps them into these Title Case shapes — this
 * interface is the stable contract the job screens code against.
 * (Moved out of `lib/mock-data.ts` when the localStorage store was retired.)
 */

export interface MaintenanceJob {
  id: string;
  acId: string;
  acLocation: string;
  type: 'Preventive' | 'Repair' | 'Emergency' | 'Inspection';
  status: 'Pending' | 'Accepted' | 'In Progress' | 'Completed' | 'Cancelled';
  priority: 'Low' | 'Medium' | 'High' | 'Critical';
  assignedTo: string;
  assignedToId?: string;
  assignedAt?: string;
  /** Set once the technician (or a manager) accepts an ASSIGNED job — the
   *  backend keeps status=ASSIGNED and records `accepted_at`. */
  acceptedAt?: string;
  reportedBy: string;
  description: string;
  scheduledDate: string;
  dueDate?: string;
  completedDate?: string;
  notes?: string;
  workPerformed?: string;
  escalatedAt?: string;
  escalationReason?: string;
}

export interface JobPart {
  name: string;
  quantity: number;
}
