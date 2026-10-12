/**
 * The ONLY browser-storage module in the app (Phase 2, task 1).
 * Purpose: offline caches for technicians (D7) — never authentication,
 * roles, or business records of truth (those live in Postgres via /api/v1).
 * eslint bans `localStorage` everywhere else.
 */
const PREFIX = "nitttr_cache_";

function available(): boolean {
  return typeof window !== "undefined";
}

export function readCache<T>(key: string, fallback: T): T {
  if (!available()) return fallback;
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeCache<T>(key: string, value: T): void {
  if (!available()) return;
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Quota exceeded etc. — a cache must never break the app.
  }
}

export function clearCache(key: string): void {
  if (!available()) return;
  window.localStorage.removeItem(PREFIX + key);
}
