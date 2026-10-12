import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Trigger a browser download for an already-fetched Blob (Phase 6 QR files,
 * Phase 7 report/audit CSVs). Authenticated endpoints cannot use a plain
 * `<a href>` — the API is Bearer-authenticated — so the bytes are fetched
 * first and handed to a synthetic anchor.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
