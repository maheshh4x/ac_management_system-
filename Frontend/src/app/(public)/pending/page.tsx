'use client';

/**
 * Account-state wall (Phase 2, task 5). Shown to authenticated users whose
 * profile is PENDING or REJECTED — the Flask status gate blocks everything
 * else server-side, so this page is the only useful screen for them.
 */
import { Clock3, LogOut, RefreshCw, XCircle } from 'lucide-react';

import { useAuth } from '@/contexts/AuthContext';

export default function PendingPage() {
  const { user, status, refreshProfile, signOut } = useAuth();
  const rejected = status === 'rejected';

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-xl">
        {rejected ? (
          <XCircle size={48} className="mx-auto text-red-500" />
        ) : (
          <Clock3 size={48} className="mx-auto text-amber-500" />
        )}
        <h1 className="mt-4 text-2xl font-bold text-slate-900">
          {rejected ? 'Registration declined' : 'Awaiting approval'}
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          {rejected
            ? 'Your access request was declined. If you believe this is a mistake, contact your campus administrator.'
            : 'Your account was created and is waiting for approval by a facility manager or administrator. You will get access as soon as it is approved.'}
        </p>
        {user?.email && (
          <p className="mt-3 text-xs text-slate-400">
            Signed in as <span className="font-semibold">{user.email}</span>
          </p>
        )}
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => void refreshProfile()}
            className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700"
          >
            <RefreshCw size={15} /> Check status
          </button>
          <button
            type="button"
            onClick={() => void signOut()}
            className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 px-4 text-sm font-semibold text-slate-600 hover:bg-slate-50"
          >
            <LogOut size={15} /> Sign out
          </button>
        </div>
      </div>
    </main>
  );
}
