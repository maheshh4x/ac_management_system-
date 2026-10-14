"use client";

/**
 * Users / approvals / notifications hooks (Phase 2, tasks 3–4).
 * Backed by the Phase 1 endpoints; route-level role checks on the server are
 * authoritative — these hooks are presentation plumbing only.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, apiList } from "./client";
import type { ApiProfile } from "@/lib/adapters/user";

export const userKeys = {
  me: ["users", "me"] as const,
  list: (q: Record<string, string | number>) => ["users", "list", q] as const,
  assignableTechnicians: ["users", "assignable-technicians"] as const,
  approvals: (status: string) => ["approvals", status] as const,
  notifications: (unread: boolean) => ["notifications", unread] as const,
};

export function useUsers(query: { role?: string; status?: string; search?: string; page?: number } = {}) {
  return useQuery({
    queryKey: userKeys.list(query),
    queryFn: () => {
      const params = new URLSearchParams();
      if (query.role) params.set("role", query.role);
      if (query.status) params.set("status", query.status);
      if (query.search) params.set("search", query.search);
      if (query.page) params.set("page", String(query.page));
      const qs = params.toString();
      return apiList<ApiProfile>(`/api/v1/users${qs ? `?${qs}` : ""}`);
    },
  });
}

export interface AssignableTechnician {
  id: string;
  name: string | null;
  email: string | null;
}

/**
 * Technician picker for the assignment workflow. Unlike useUsers (MANAGE_USERS,
 * SUPER_ADMIN only), this endpoint grants ASSIGN_MAINTENANCE holders the
 * least-privilege list they need — otherwise managers see an empty dropdown.
 */
export function useAssignableTechnicians(enabled = true) {
  return useQuery({
    queryKey: userKeys.assignableTechnicians,
    enabled,
    queryFn: () =>
      api<{ technicians: AssignableTechnician[]; total: number }>(
        "/api/v1/users/assignable-technicians",
      ),
  });
}

export function useApprovals(
  status: "PENDING" | "APPROVED" | "REJECTED" = "PENDING",
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: userKeys.approvals(status),
    queryFn: () => apiList<ApiProfile>(`/api/v1/approvals?status=${status}`),
    // Callers without MANAGE_USERS would just collect a 403; disable instead.
    enabled: options.enabled ?? true,
  });
}

export function useApproveUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, role }: { id: string; role?: string }) =>
      api(`/api/v1/approvals/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        body: role ? { role } : {},
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["approvals"] });
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export function useRejectUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api(`/api/v1/approvals/${encodeURIComponent(id)}/reject`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["approvals"] });
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export function usePatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...patch
    }: { id: string; role?: string; is_active?: boolean; reporting_manager_id?: string | null }) =>
      api(`/api/v1/users/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  body: string | null;
  entity_type: string | null;
  entity_id: string | null;
  is_read: boolean;
  created_at: string;
}

export function useNotifications(unreadOnly = false) {
  return useQuery({
    queryKey: userKeys.notifications(unreadOnly),
    queryFn: () =>
      api<{ items: NotificationItem[]; unread_count: number }>(
        `/api/v1/notifications${unreadOnly ? "?unread=1" : ""}`,
      ),
    refetchInterval: 60_000,
  });
}

export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/notifications/${encodeURIComponent(id)}/read`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useMarkAllNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/v1/notifications/read-all", { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}
