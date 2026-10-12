"use client";

/**
 * Pending-sync queue for job actions performed offline (Phase 4, task 3).
 *
 * The ONLY job data allowed to live in browser storage is this queue of
 * not-yet-delivered mutations — everything else is read from the API. The
 * queue itself sits in IndexedDB (`idb-keyval`, keyval-store) rather than
 * localStorage: job state is never persisted, only the mutations the server
 * has not seen yet.
 *
 * Sync protocol: `POST /api/v1/maintenance/sync` answers per item with
 * `applied | duplicate | rejected{code,message}`; `mergeSyncResults` folds
 * that answer back into the queue — applied/duplicate leave, rejected stay
 * visible with the server's reason, anything unanswered stays pending.
 */
import { get, set } from "idb-keyval";

/** Lifecycle actions the sync endpoint accepts (mirrors backend ACTIONS). */
export type JobAction = "assign" | "accept" | "start" | "complete" | "escalate" | "cancel";

export interface QueuedJobMutation {
  /** Stable id — the server dedupes replays on it (`crypto.randomUUID()`). */
  client_mutation_id: string;
  action: JobAction;
  job_id: string;
  payload: Record<string, unknown>;
  queued_at: number;
  /** Human summary for the sync panel, e.g. "Accept job". */
  label: string;
  state: "pending" | "rejected";
  /** Populated when the server rejected the item: "CODE: message". */
  reason?: string;
}

export interface SyncResultItem {
  client_mutation_id?: string;
  result: "applied" | "duplicate" | "rejected";
  code?: string;
  message?: string;
}

const QUEUE_KEY = "acms.pending-job-sync";

/** IndexedDB is unavailable in SSR/jsdom — fall back to memory (no-op
 *  persistence) so reads/writes never throw outside a real browser. */
let memoryQueue: QueuedJobMutation[] = [];

function idbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

export async function readQueue(): Promise<QueuedJobMutation[]> {
  if (!idbAvailable()) return [...memoryQueue];
  try {
    return (await get<QueuedJobMutation[]>(QUEUE_KEY)) ?? [];
  } catch {
    return [...memoryQueue];
  }
}

export async function writeQueue(items: QueuedJobMutation[]): Promise<void> {
  if (!idbAvailable()) {
    memoryQueue = items;
    return;
  }
  try {
    await set(QUEUE_KEY, items);
  } catch {
    memoryQueue = items;
  }
}

/**
 * Fold the sync endpoint's per-item results into the queue (pure):
 * - `applied` / `duplicate` → delivered, removed;
 * - `rejected` → kept with the server's `CODE: message` reason visible;
 * - no answer for an item → still pending (kept for the next attempt).
 */
export function mergeSyncResults(
  queue: QueuedJobMutation[],
  results: SyncResultItem[],
): QueuedJobMutation[] {
  const byId = new Map<string, SyncResultItem>();
  for (const item of results) {
    if (item.client_mutation_id) byId.set(item.client_mutation_id, item);
  }
  const merged: QueuedJobMutation[] = [];
  for (const item of queue) {
    const answer = byId.get(item.client_mutation_id);
    if (!answer) {
      merged.push(item); // server didn't answer this one — retry later
    } else if (answer.result === "applied" || answer.result === "duplicate") {
      // delivered (duplicate = already applied on an earlier attempt)
    } else {
      merged.push({
        ...item,
        state: "rejected",
        reason: `${answer.code ?? "ERROR"}: ${answer.message ?? "Rejected by the server."}`,
      });
    }
  }
  return merged;
}
