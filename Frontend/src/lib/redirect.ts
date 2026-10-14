/**
 * Post-login destination handling (QR-scan workflow).
 *
 * After login the app returns the user to the page they originally
 * requested (e.g. a scanned `/a/<uuid>` QR landing or a deep portal link
 * saved as `?next=` by the edge proxy). Only same-origin app paths are ever
 * honored — anything else falls back to the role home, so a crafted `next`
 * can never become an open redirect.
 */

const ALLOWED_PREFIXES = ["/a/", "/admin/", "/manager/", "/technician/", "/viewer/", "/campus/"];

export function getSafeNext(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const value = raw.trim();
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  if (value.includes("\\") || value.includes("://")) return null;
  const path = value.split("?")[0].split("#")[0];
  if (path.includes("/../") || path.endsWith("/..") || path === "/..") return null;
  const allowed = path === "/campus" || ALLOWED_PREFIXES.some((prefix) => path.startsWith(prefix));
  if (!allowed) return null;
  return value;
}

/** Read `?next=` from the current location (client-only, SSR-safe). */
export function nextFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return getSafeNext(new URLSearchParams(window.location.search).get("next"));
  } catch {
    return null;
  }
}

/** Login URL preserving the destination the user originally requested. */
export function loginUrl(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}
