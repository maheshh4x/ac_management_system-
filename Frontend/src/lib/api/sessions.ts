import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface UsageSession {
  id: string;
  ac_id: string;
  started_by: string | null;
  started_at: string | null;
  ended_at: string | null;
  /** Seconds between start and end (live sessions: start → now). */
  duration_seconds: number | null;
  start_note: string | null;
  end_note: string | null;
}

const sessionKeys = {
  all: ["sessions"] as const,
  asset: (ref: string) => [...sessionKeys.all, ref] as const,
};

/** GET /api/v1/assets/<ref>/sessions — VIEW_ASSET. Recent ON/OFF history. */
export function useAssetSessions(ref: string | null, limit = 20) {
  return useQuery({
    queryKey: sessionKeys.asset(ref ?? ""),
    enabled: Boolean(ref),
    queryFn: async (): Promise<UsageSession[]> =>
      api<UsageSession[]>(
        `/api/v1/assets/${encodeURIComponent(ref!)}/sessions?limit=${limit}`,
      ),
  });
}

function useInvalidateSessions() {
  const qc = useQueryClient();
  return (ref: string) => {
    void qc.invalidateQueries({ queryKey: sessionKeys.asset(ref) });
  };
}

/** POST …/sessions/start — RECORD_USAGE_SESSION. 409 when one is already open. */
export function useStartSession(ref: string) {
  const invalidate = useInvalidateSessions();
  return useMutation({
    mutationFn: (note?: string | null) =>
      api<UsageSession>(`/api/v1/assets/${encodeURIComponent(ref)}/sessions/start`, {
        method: "POST",
        body: { note: note ?? null },
      }),
    onSettled: () => invalidate(ref),
  });
}

/** POST …/sessions/stop — RECORD_USAGE_SESSION. 409 when none is open. */
export function useStopSession(ref: string) {
  const invalidate = useInvalidateSessions();
  return useMutation({
    mutationFn: (note?: string | null) =>
      api<UsageSession>(`/api/v1/assets/${encodeURIComponent(ref)}/sessions/stop`, {
        method: "POST",
        body: { note: note ?? null },
      }),
    onSettled: () => invalidate(ref),
  });
}

/** Human "1h 4m" rendering for a duration in seconds. */
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds) || totalSeconds < 0) return "—";
  const minutes = Math.floor(totalSeconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${Math.floor(totalSeconds)}s`;
}
