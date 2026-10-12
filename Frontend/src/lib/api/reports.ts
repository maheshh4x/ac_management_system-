"use client";

/**
 * Report download helpers (Phase 7, FE task 2): `?format=csv` on every
 * `/api/v1/reports/*` endpoint (02-api-contract.md).
 *
 * All five report endpoints share the same query contract:
 *
 *   from, to   YYYY-MM-DD (400 INVALID_DATE). Meaning differs per report —
 *              the asset reports filter `installation_date`,
 *              due-for-maintenance filters `next_service_date`, and
 *              maintenance-trend bounds the month bucket window (max 24
 *              months, 400 INVALID_RANGE beyond that).
 *   block      case-insensitive substring on block_id / block_name
 *   format     `csv` streams a UTF-8 attachment; anything else is 400
 *              UNSUPPORTED_FORMAT
 *
 * Every report is gated on VIEW_REPORTS — there is no separate export
 * permission, so a role that can see the chart can download it.
 *
 * CSV bodies are fetched through `apiBlob` because the API is
 * Bearer-authenticated: a plain `<a href>` would arrive without a token.
 */
import { apiBlob } from "./client";

export interface ReportFilters {
  /** YYYY-MM-DD inclusive. */
  from?: string;
  /** YYYY-MM-DD inclusive. */
  to?: string;
  /** Block id or name (case-insensitive substring match). */
  block?: string;
}

export type ReportName =
  | "assets-by-block"
  | "assets-by-type"
  | "capacity"
  | "due-for-maintenance"
  | "maintenance-trend";

/** Reports offered as a CSV download from the analytics module. */
export const CSV_REPORTS: ReadonlyArray<{ name: ReportName; label: string }> = [
  { name: "due-for-maintenance", label: "Due for maintenance (row-level)" },
  { name: "assets-by-block", label: "Assets by block" },
  { name: "assets-by-type", label: "Assets by type" },
  { name: "capacity", label: "Installed capacity" },
  { name: "maintenance-trend", label: "Maintenance trend (monthly)" },
];

export function reportQueryString(filters: ReportFilters, format?: "csv"): string {
  const params = new URLSearchParams();
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.block) params.set("block", filters.block);
  if (format) params.set("format", format);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Fetch one report as CSV using the same filters the charts are showing. */
export function fetchReportCsv(name: ReportName, filters: ReportFilters): Promise<Blob> {
  return apiBlob(`/api/v1/reports/${name}${reportQueryString(filters, "csv")}`);
}

/** Suggested download filename for a report CSV. */
export function reportFilename(name: ReportName): string {
  const stamp = new Date().toISOString().slice(0, 10);
  return `acms_${name}_${stamp}.csv`;
}
