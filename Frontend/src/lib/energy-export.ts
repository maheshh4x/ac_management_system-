/**
 * Client-side CSV export for the Energy Management Dashboard (mirrors the
 * ReportsAnalyticsModule visible-rows export: computed rows the user already
 * sees, downloaded locally — no new backend surface).
 */

export function toCsvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function buildEnergyCsv(
  headers: string[],
  rows: Array<Array<string | number | null | undefined>>,
): string {
  const lines = [headers.map(toCsvCell).join(",")];
  for (const row of rows) lines.push(row.map(toCsvCell).join(","));
  return `${lines.join("\n")}\n`;
}

/** Triggers a download in the browser (no-op helper kept separate for tests). */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
