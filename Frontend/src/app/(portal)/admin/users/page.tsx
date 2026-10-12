'use client';

/**
 * Admin user management (Phase 2): real API-backed list + role/active-state
 * management via Phase 1 endpoints. Account creation happens through the
 * signup → approval pipeline (see /admin/approvals); administrators cannot
 * set passwords — credentials belong to Supabase Auth.
 */
import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Search, UserX, UserCheck, ShieldCheck, UserPlus } from 'lucide-react';
import Link from 'next/link';

import { mapProfileToManagedUser } from '@/lib/adapters/user';
import { usePatchUser, useUsers } from '@/lib/api/users';
import { useAuth } from '@/contexts/AuthContext';
import { ApiError } from '@/lib/api/client';
import type { Role } from '@/lib/permissions';

const ROLE_COLORS: Record<string, string> = {
  SUPER_ADMIN: 'bg-purple-100 text-purple-700 border-purple-200',
  FACILITY_MANAGER: 'bg-blue-100 text-blue-700 border-blue-200',
  TECHNICIAN: 'bg-teal-100 text-teal-700 border-teal-200',
  VIEWER: 'bg-slate-100 text-slate-600 border-slate-200',
};

const ROLES: Role[] = ['SUPER_ADMIN', 'FACILITY_MANAGER', 'TECHNICIAN', 'VIEWER'];

export default function UsersPage() {
  const [search, setSearch] = useState('');
  const [pageError, setPageError] = useState('');
  const { user: currentUser } = useAuth();

  const usersQuery = useUsers(search ? { search } : {});
  const patchUser = usePatchUser();

  const users = useMemo(
    () => (usersQuery.data?.items ?? []).map(mapProfileToManagedUser),
    [usersQuery.data],
  );
  const filtered = users.filter(u =>
    [u.name, u.email, u.role].join(' ').toLowerCase().includes(search.toLowerCase()),
  );

  const runPatch = (id: string, patch: { role?: Role; is_active?: boolean }) => {
    setPageError('');
    patchUser.mutate(
      { id, ...patch },
      {
        onError: (err) => {
          if (err instanceof ApiError && err.code === 'LAST_SUPER_ADMIN') {
            setPageError('The last active super admin cannot be demoted or deactivated.');
          } else {
            setPageError(err instanceof Error ? err.message : 'Update failed.');
          }
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">User & Role Management</h1>
          <p className="text-sm text-slate-500 mt-1">Manage system users, assign roles and control access.</p>
        </div>
        <Link href="/admin/approvals" className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm transition-colors">
          <UserPlus size={16} /> Review sign-up requests
        </Link>
      </div>

      {/* Role overview tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {ROLES.map(role => {
          const count = users.filter(u => u.role === role).length;
          return (
            <div key={role} className={`rounded-xl border p-4 ${ROLE_COLORS[role]}`}>
              <div className="flex items-center gap-2 mb-1">
                <ShieldCheck size={14} />
                <span className="text-xs font-semibold uppercase tracking-wider">{role.replace('_', ' ')}</span>
              </div>
              <p className="text-2xl font-bold">{count}</p>
              <p className="text-xs opacity-70">users</p>
            </div>
          );
        })}
      </div>

      {/* Search */}
      <div className="relative max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input type="text" placeholder="Search by name, email, role..." className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none" value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {pageError && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{pageError}</p>
      )}
      {usersQuery.isError && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          Could not load users. {usersQuery.error instanceof Error ? usersQuery.error.message : ''}
        </p>
      )}
      {usersQuery.isLoading && <p className="text-sm text-slate-500">Loading users…</p>}

      {/* Users Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {['User', 'Email', 'Role', 'Status', 'Created', 'Actions'].map(h => (
                  <th key={h} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filtered.map((u, i) => {
                const isSelf = currentUser?.id === u.id;
                return (
                  <motion.tr key={u.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.03 }} className="hover:bg-slate-50 transition-colors group">
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-purple-500 text-white text-xs font-bold flex items-center justify-center flex-shrink-0">
                          {u.name.split(' ').map(n => n[0]).slice(0, 2).join('')}
                        </div>
                        <div>
                          <p className="font-semibold text-slate-900">{u.name || '—'}</p>
                          <p className="text-xs text-slate-400">{u.phone}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-slate-600">{u.email}</td>
                    <td className="px-4 py-3.5">
                      <select
                        aria-label={`Role for ${u.email}`}
                        value={u.role}
                        disabled={isSelf || patchUser.isPending}
                        onChange={(e) => runPatch(u.id, { role: e.target.value as Role })}
                        className={`px-2 py-1 rounded-full text-xs font-semibold border bg-white focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-60 ${ROLE_COLORS[u.role]}`}
                      >
                        {ROLES.map(role => (
                          <option key={role} value={role}>{role.replace('_', ' ')}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3.5">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${u.status === 'pending' ? 'bg-amber-100 text-amber-700 border-amber-200' : u.status === 'rejected' ? 'bg-red-100 text-red-700 border-red-200' : u.isActive === false ? 'bg-slate-100 text-slate-600 border-slate-200' : 'bg-emerald-100 text-emerald-700 border-emerald-200'}`}>
                        {u.status === 'pending' ? 'Pending' : u.status === 'rejected' ? 'Rejected' : u.isActive === false ? 'Inactive' : 'Active'}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-slate-500 text-xs">{u.createdAt?.slice(0, 10)}</td>
                    <td className="px-4 py-3.5">
                      {isSelf ? (
                        <span className="text-xs text-slate-400">This is you</span>
                      ) : (
                        <button
                          onClick={() => runPatch(u.id, { is_active: u.isActive === false })}
                          disabled={patchUser.isPending}
                          className={`p-1.5 rounded-md transition-colors disabled:opacity-50 ${u.isActive !== false ? 'hover:bg-red-100 text-red-500' : 'hover:bg-emerald-100 text-emerald-600'}`}
                          title={u.isActive !== false ? 'Deactivate' : 'Activate'}
                        >
                          {u.isActive !== false ? <UserX size={14} /> : <UserCheck size={14} />}
                        </button>
                      )}
                    </td>
                  </motion.tr>
                );
              })}
              {filtered.length === 0 && !usersQuery.isLoading && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-slate-400">No users found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
