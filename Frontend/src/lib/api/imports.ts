"use client";

/**
 * Bulk import (Phase 3 task 4): upload → dry-run report → confirm → commit.
 * The backend owns parsing/validation (ONE transaction: chunked statements
 * with savepoint fallback to per-row isolation); the modal only renders the
 * report and counts.
 *
 * Commits send an `Idempotency-Key` derived from the file CONTENT, so a
 * retry after a timeout either replays the first attempt's report
 * (`replayed: true`) or gets 409 `IMPORT_IN_PROGRESS` — never a second
 * blind import of the same bytes.
 */
import { ApiError, IMPORT_TIMEOUT_MS, apiFormData } from "./client";

export interface ImportReportError {
  row: number;
  message: string;
}

export interface ImportReportWarning {
  row?: number;
  kind?: string;
  message: string;
}

export interface ImportReportRow {
  row: number;
  ac_id?: string;
  action: "create" | "update" | "duplicate" | "invalid" | "skip";
  message?: string;
}

export interface RoomDetail {
  reference: string;
  count: number;
  rows: number[];
}

/** Mirrors the report contract of POST /api/v1/assets/import. */
export interface ImportReport {
  dry_run: boolean;
  /** Parsed records (physical rows minus ignored blank rows). */
  total_rows: number;
  /** Rows physically present in the file body, including blank padding. */
  physical_rows: number;
  /** Blank rows (null/empty/whitespace cells only) dropped before validation. */
  blank_rows_ignored: number;
  imported: number;
  created: number;
  updated: number;
  duplicates: number;
  invalid: number;
  pending_location: number;
  failed: number;
  errors: ImportReportError[];
  warnings: ImportReportWarning[];
  rows: ImportReportRow[];
  missing_fields: string[];
  recognized_columns: string[];
  unknown_columns: string[];
  ignored_columns: string[];
  unmatched_rooms: string[];
  ambiguous_rooms: string[];
  unmatched_room_details: RoomDetail[];
  ambiguous_room_details: RoomDetail[];
  canonical_format: boolean;
  generated_identities: number;
  attributes_recorded: number;
  /** True when the backend replayed a completed identical import instead of running again. */
  replayed?: boolean;
}

/** Canonical 22-column format accepted by the import endpoint. */
export const TEMPLATE_COLUMNS = [
  "college_name",
  "block_name",
  "floor_name",
  "department_name",
  "room_type",
  "room_number",
  "ac_make_company",
  "unit_number",
  "ac_model_number",
  "ac_serial_number",
  "capacity_ton",
  "type",
  "manufacturing_year",
  "installation_year",
  "star_rating",
  "power_factor",
  "status",
  "last_maintenance_type",
  "last_maintenance_date",
  "next_maintenance_date",
  "technician",
  "remarks",
];

/** SHA-256 of the file bytes (hex) — the content-addressed idempotency key.
 * Returns null when Web Crypto is unavailable (non-secure context); the
 * backend then just runs the import normally, which stays data-safe thanks
 * to the ac_id unique constraint + upserts. */
async function contentKey(file: File): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return null;
  }
}

/** One multipart call; `dryRun` picks validation (200) vs commit (201). */
export async function uploadAssetImport(file: File, dryRun: boolean): Promise<ImportReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("dry_run", dryRun ? "true" : "false");
  const headers: Record<string, string> = {};
  if (!dryRun) {
    const key = await contentKey(file);
    if (key) headers["Idempotency-Key"] = key;
  }
  return apiFormData<ImportReport>(
    `/api/v1/assets/import${dryRun ? "?dry_run=true" : ""}`,
    form,
    headers,
    // Bulk commit budget (120 s); dry runs stay on the normal 15 s budget.
    dryRun ? undefined : IMPORT_TIMEOUT_MS,
  );
}

/** True when NO response confirmed the outcome (timeout / unreachable):
 * the backend may still have committed, so the result is unknown. */
export function isUncertainImportFailure(err: unknown): boolean {
  return err instanceof ApiError && (err.code === "TIMEOUT" || err.code === "NETWORK_ERROR");
}

/** User-facing commit failure text. Never claims "no data changed" unless
 * the server itself confirmed the outcome (4xx/5xx envelope). */
export function describeImportFailure(err: unknown): string {
  if (!(err instanceof ApiError)) {
    return "The import failed. The result is unknown — verify before retrying.";
  }
  if (err.code === "TIMEOUT") {
    return (
      "The import request timed out while the server was processing it. " +
      "The result may still have been committed. Verify the import before retrying."
    );
  }
  if (err.code === "NETWORK_ERROR") {
    return (
      "The connection to the server was lost while the import was running. " +
      "The result may still have been committed. Verify the import before retrying."
    );
  }
  if (err.code === "IMPORT_IN_PROGRESS") {
    return (
      "The server is still processing an identical import. Wait a moment, then " +
      "check the asset list before retrying."
    );
  }
  // The backend answered and owns the outcome: its message is authoritative
  // (4xx validation never wrote; 500 IMPORT_FAILED rolls the transaction back).
  if (err.status >= 400) {
    return `${err.message} No data was changed.`;
  }
  return err.message;
}

/** Browser-side template download (canonical columns, one example row). */
export function downloadImportTemplate(): void {
  const example = [
    "NITTTR Chennai",
    "Main Academic Block",
    "Floor 1",
    "Computer Science",
    "Lab",
    "CSE Computer Lab 1",
    "Daikin",
    "1",
    "FTKM50",
    "",
    "1.5",
    "Split",
    "2022",
    "2023",
    "5",
    "0.95",
    "Working",
    "preventive",
    "",
    "",
    "",
    "",
  ];
  const csv = [TEMPLATE_COLUMNS.join(","), example.map((v) => `"${v}"`).join(",")].join("\r\n") + "\r\n";
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "ac-import-template.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
