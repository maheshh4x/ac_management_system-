'use client';

/**
 * Issue-report API client (Features B/C): file a report, track its status,
 * review the Super Admin queue, approve/reject. All authorized roles may
 * submit; only APPROVE_REPORTS (SUPER_ADMIN) may decide — the backend
 * enforces both, the hooks below only surface the results.
 */
import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';

import { api } from './client';

export type ReportCategory =
  | 'AC_NOT_COOLING'
  | 'AC_NOT_STARTING'
  | 'WATER_LEAKAGE'
  | 'NOISE_VIBRATION'
  | 'ELECTRICAL_POWER'
  | 'OUTDOOR_UNIT'
  | 'INDOOR_UNIT'
  | 'PREVENTIVE_MAINTENANCE'
  | 'OTHER';

export type ReportStatus = 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED';

export const REPORT_CATEGORIES: { value: ReportCategory; label: string }[] = [
  { value: 'AC_NOT_COOLING', label: 'AC not cooling' },
  { value: 'AC_NOT_STARTING', label: 'AC not starting' },
  { value: 'WATER_LEAKAGE', label: 'Water leakage' },
  { value: 'NOISE_VIBRATION', label: 'Unusual noise or vibration' },
  { value: 'ELECTRICAL_POWER', label: 'Electrical or power concern' },
  { value: 'OUTDOOR_UNIT', label: 'Outdoor unit problem' },
  { value: 'INDOOR_UNIT', label: 'Indoor unit problem' },
  { value: 'PREVENTIVE_MAINTENANCE', label: 'Preventive maintenance' },
  { value: 'OTHER', label: 'Other' },
];

export function reportCategoryLabel(category: string | null | undefined): string {
  return REPORT_CATEGORIES.find((c) => c.value === category)?.label ?? category ?? '—';
}

export function reportStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'PENDING_APPROVAL':
      return 'Pending approval';
    case 'APPROVED':
      return 'Approved';
    case 'REJECTED':
      return 'Rejected';
    default:
      return status ?? '—';
  }
}

export interface IssueReport {
  id: string;
  report_code: string;
  ac_id: string | null;
  reporter_id: string | null;
  category: ReportCategory;
  description: string;
  priority: string;
  location_text: string | null;
  asset_location_snapshot: string | null;
  status: ReportStatus;
  decided_by: string | null;
  decided_at: string | null;
  rejection_reason: string | null;
  work_order_id: string | null;
  created_at: string;
  updated_at: string;
  deduplicated?: boolean;
}

export interface IssueReportDetail extends IssueReport {
  work_order: {
    id: string;
    ac_id: string | null;
    maintenance_type: string | null;
    priority: string | null;
    status: string | null;
    technician_id: string | null;
    scheduled_date: string | null;
    created_at: string | null;
  } | null;
  decided_by_profile: { id: string; name: string | null; role: string | null } | null;
  asset_code?: string | null;
  reporter_name?: string | null;
}

export interface CreateReportInput {
  ac_id: string;
  category: ReportCategory;
  description: string;
  priority?: string;
  location_text?: string;
  /** One UUID per form instance — retries return the original report. */
  idempotency_key?: string;
}

export const issueReportKeys = {
  all: ['issue-reports'] as const,
  mine: ['issue-reports', 'mine'] as const,
  pending: ['issue-reports', 'pending'] as const,
  detail: (id: string) => ['issue-reports', id] as const,
};

export function useCreateIssueReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateReportInput) =>
      api<IssueReport>('/api/v1/issue-reports', {
        method: 'POST',
        body: input,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: issueReportKeys.all });
    },
  });
}

export function useMyReports(enabled = true) {
  return useQuery({
    queryKey: issueReportKeys.mine,
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api<{ reports: IssueReport[]; pagination: { page: number; page_size: number; total: number } }>(
        '/api/v1/issue-reports/mine?page_size=100',
      ),
  });
}

export function usePendingReports(enabled = true) {
  return useQuery({
    queryKey: issueReportKeys.pending,
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api<{
        reports: (IssueReport & { asset_code?: string | null; reporter_name?: string | null })[];
        pagination: { page: number; page_size: number; total: number };
      }>('/api/v1/issue-reports/pending?page_size=100'),
  });
}

export const issueReportAllKey = ['issue-reports', 'all'] as const;

/** Administrative tracking: every report including decided ones. */
export function useAllReports(enabled = true) {
  return useQuery({
    queryKey: issueReportAllKey,
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api<{
        reports: (IssueReport & {
          asset_code?: string | null;
          reporter_name?: string | null;
          work_order_status?: string | null;
        })[];
        pagination: { page: number; page_size: number; total: number };
      }>('/api/v1/issue-reports?page_size=100'),
  });
}

export function useIssueReport(id: string | null, enabled = true) {
  return useQuery({
    queryKey: issueReportKeys.detail(id ?? ''),
    enabled: Boolean(id) && enabled,
    queryFn: () => api<IssueReportDetail>(`/api/v1/issue-reports/${encodeURIComponent(id as string)}`),
  });
}

export function useApproveReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ id: string | null; report_id: string; report_code: string; status: string; already_processed: boolean }>(
        `/api/v1/issue-reports/${encodeURIComponent(id)}/approve`,
        { method: 'POST' },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: issueReportKeys.all });
      void queryClient.invalidateQueries({ queryKey: ['maintenance'] });
    },
  });
}

export function useRejectReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api<IssueReport>(`/api/v1/issue-reports/${encodeURIComponent(id)}/reject`, {
        method: 'POST',
        body: { reason },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: issueReportKeys.all });
    },
  });
}
