'use client';

/**
 * My Reports page (Features B/C): the reporter's own issue reports with
 * live statuses, plus the application-level "Report Issue" entry point
 * (asset typed in by the reporter, validated server-side). Rendered inside
 * every portal; the backend scopes rows to the session identity.
 */
import React, { useState } from 'react';
import { Plus } from 'lucide-react';

import { MyReportsView } from '@/components/features/issues/MyReportsView';
import { ReportIssueModal } from '@/components/features/issues/ReportIssueModal';

export function MyReportsPage() {
  const [showReport, setShowReport] = useState(false);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">My Reports</h1>
          <p className="mt-1 text-sm text-slate-500">
            Reports you filed and their current status. New reports start pending Super Admin approval.
          </p>
        </div>
        <button
          onClick={() => setShowReport(true)}
          className="flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-bold text-white shadow-sm hover:bg-blue-700"
        >
          <Plus size={16} /> Report Issue
        </button>
      </div>
      <MyReportsView />
      {showReport && (
        <ReportIssueModal allowAssetEntry onClose={() => setShowReport(false)} />
      )}
    </div>
  );
}
