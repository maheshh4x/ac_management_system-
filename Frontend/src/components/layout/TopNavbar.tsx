'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Bell, ShieldCheck, User, LogOut, UserCircle, ChevronDown } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { GlobalSearch } from '@/components/features/GlobalSearch';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { useMarkAllNotificationsRead, useMarkNotificationRead, useNotifications, type NotificationItem } from '@/lib/api/users';
import { useDismissQueued, useSyncNow, useSyncQueue } from '@/lib/api/maintenance';

const ROLE_LABELS: Record<string, string> = {
  SUPER_ADMIN: 'Admin',
  FACILITY_MANAGER: 'Faculty Manager',
  TECHNICIAN: 'Technician',
  VIEWER: 'Viewer',
};

const ROLE_COLORS: Record<string, string> = {
  SUPER_ADMIN: 'bg-blue-600',
  FACILITY_MANAGER: 'bg-emerald-600',
  TECHNICIAN: 'bg-amber-500',
  VIEWER: 'bg-slate-500',
};

export function TopNavbar() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Server-backed notifications — the backend scopes them to the signed-in
  // user and this hook polls every 60s.
  const { data: notificationData } = useNotifications();
  const notifications = notificationData?.items ?? [];
  const unreadCount = notificationData?.unread_count ?? 0;
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const roleBase = user?.role === 'SUPER_ADMIN' ? '/admin'
    : user?.role === 'FACILITY_MANAGER' ? '/manager'
    : user?.role === 'TECHNICIAN' ? '/technician'
    : '/viewer';

  const profileHref = `${roleBase}/profile`;
  const avatarBg = ROLE_COLORS[user?.role ?? ''] ?? 'bg-slate-500';
  const roleLabel = ROLE_LABELS[user?.role ?? ''] ?? (user?.role ?? '');

  const openNotification = (notification: NotificationItem) => {
    markRead.mutate(notification.id);
    if (notification.entity_type === 'maintenance' && notification.entity_id) {
      const href = user?.role === 'TECHNICIAN'
        ? `/technician/jobs/${encodeURIComponent(notification.entity_id)}`
        : `${roleBase}/maintenance`;
      router.push(href);
    } else if (notification.entity_type === 'asset_movement' && notification.entity_id) {
      // Phase 5: movement decisions → the movement list for this role.
      const href = user?.role === 'TECHNICIAN'
        ? '/technician/movement'
        : `${roleBase}/maintenance?tab=movement`;
      router.push(href);
    }
  };

  return (
    <header data-print="hide" className="h-16 bg-white border-b border-slate-200 px-6 sticky top-0 z-30 flex items-center justify-between shadow-2xs">
      {/* Global Search Bar */}
      <div className="flex-1 max-w-md">
        <GlobalSearch />
      </div>

      {/* Right Controls */}
      <div className="flex items-center gap-3">
        {/* Role Badge */}
        {user?.role && (
          <div className="hidden sm:flex items-center gap-1.5 px-3 py-1 bg-slate-100 text-slate-700 rounded-full text-xs font-semibold border border-slate-200">
            <ShieldCheck size={14} className="text-blue-600" />
            <span>{roleLabel}</span>
          </div>
        )}

        {/* Notifications */}
        <div className="relative">
          <button
            aria-label="Notifications"
            onClick={() => setNotificationsOpen(value => !value)}
            className="relative p-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <Bell size={18} />
            {!!unreadCount && <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-0.5 bg-blue-600 text-white rounded-full text-[9px] font-bold flex items-center justify-center">{unreadCount}</span>}
          </button>
          <AnimatePresence>
            {notificationsOpen && (
              <motion.div initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} className="absolute right-0 mt-2 w-80 bg-white rounded-xl shadow-xl border border-slate-200 z-50 overflow-hidden">
                <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><span className="font-semibold text-sm text-slate-900">Notifications</span>{!!unreadCount && <button onClick={() => markAllRead.mutate()} className="text-xs font-semibold text-blue-600">Mark all read</button>}</div>
                {notifications.length ? notifications.slice(0, 8).map(notification => <button type="button" key={notification.id} onClick={() => openNotification(notification)} className={`block w-full border-b border-slate-50 px-4 py-3 text-left text-xs text-slate-600 hover:bg-slate-50 ${notification.is_read ? 'bg-white' : 'bg-blue-50/50'}`}><span className={notification.is_read ? 'text-slate-600' : 'font-semibold text-slate-800'}>{notification.title}</span>{notification.body && <p className="mt-0.5 truncate text-slate-500">{notification.body}</p>}<div className="mt-1 text-[10px] text-slate-400">{new Date(notification.created_at).toLocaleString()}{!notification.is_read && <span className="ml-2 font-bold text-blue-600">Unread</span>}</div></button>) : <div className="px-4 py-6 text-sm text-slate-500 text-center">No notifications yet</div>}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        <SyncQueueMenu />

        {/* User Avatar / Profile Dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen(v => !v)}
            className="flex items-center gap-2 pl-2 border-l border-slate-200 hover:bg-slate-50 rounded-lg pr-2 py-1 transition-colors group"
            aria-label="User menu"
          >
            <div className={`w-8 h-8 rounded-full ${avatarBg} text-white flex items-center justify-center font-bold text-xs shadow-sm`}>
              {user?.name ? user.name.slice(0, 2).toUpperCase() : <User size={16} />}
            </div>
            <div className="hidden md:flex flex-col text-left">
              <span className="text-xs font-bold text-slate-900 leading-none">{user?.name || 'User'}</span>
              <span className="text-[10px] text-slate-500 font-medium mt-0.5 leading-none">{user?.email || ''}</span>
            </div>
            <ChevronDown size={14} className={`text-slate-400 transition-transform duration-200 ${dropdownOpen ? 'rotate-180' : ''}`} />
          </button>

          <AnimatePresence>
            {dropdownOpen && (
              <motion.div
                initial={{ opacity: 0, scale: 0.95, y: -6 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: -6 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 mt-2 w-56 bg-white rounded-xl shadow-xl border border-slate-200 py-1.5 z-50 overflow-hidden"
              >
                {/* User info header */}
                <div className="px-4 py-3 border-b border-slate-100">
                  <div className="flex items-center gap-3">
                    <div className={`w-9 h-9 rounded-full ${avatarBg} text-white flex items-center justify-center font-bold text-sm shadow-sm`}>
                      {user?.name ? user.name.slice(0, 2).toUpperCase() : <User size={16} />}
                    </div>
                    <div>
                      <div className="text-sm font-bold text-slate-900 leading-tight">{user?.name || 'User'}</div>
                      <div className="text-[11px] text-slate-500 leading-tight truncate max-w-[130px]">{user?.email || ''}</div>
                      <div className="text-[10px] text-blue-600 font-semibold mt-0.5">{roleLabel}</div>
                    </div>
                  </div>
                </div>

                {/* Menu items */}
                <button
                  onClick={() => { setDropdownOpen(false); router.push(profileHref); }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <UserCircle size={16} className="text-slate-400" />
                  My Profile
                </button>

                <div className="border-t border-slate-100 mt-1 pt-1">
                  <button
                    onClick={() => { setDropdownOpen(false); void signOut(); }}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-600 hover:bg-red-50 transition-colors font-semibold"
                  >
                    <LogOut size={16} />
                    Sign Out
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}

/**
 * Badge + panel for the offline job-action queue (IndexedDB via idb-keyval).
 * Lives in its own component so its open state resets naturally when the
 * queue empties (the parent stops rendering it) — no state-set-in-effect.
 * Auto-replays on mount while online and whenever the connection returns.
 */
function SyncQueueMenu() {
  const [open, setOpen] = useState(false);
  const { data: syncQueue } = useSyncQueue();
  const { mutate: syncMutate, isPending: syncing, data: syncResult } = useSyncNow();
  const dismissQueued = useDismissQueued();
  const queueLength = syncQueue?.length ?? 0;

  useEffect(() => {
    if (queueLength === 0) return;
    const sync = () => syncMutate();
    window.addEventListener('online', sync);
    if (navigator.onLine) sync();
    return () => window.removeEventListener('online', sync);
  }, [queueLength, syncMutate]);

  if (!queueLength) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(value => !value)}
        className="hidden rounded-full bg-amber-50 px-2 py-1 text-[10px] font-bold text-amber-700 hover:bg-amber-100 transition-colors sm:inline cursor-pointer"
      >
        {queueLength} pending sync
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -5 }}
            className="absolute right-0 mt-2 w-80 bg-white rounded-xl shadow-xl border border-slate-200 z-50 overflow-hidden"
          >
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <span className="font-semibold text-sm text-slate-900">Pending sync</span>
              <button
                onClick={() => syncMutate()}
                disabled={syncing}
                className="text-xs font-semibold text-blue-600 disabled:opacity-50 cursor-pointer"
              >
                {syncing ? 'Syncing…' : 'Sync now'}
              </button>
            </div>
            {syncResult && (
              <p className="border-b border-slate-100 px-4 py-2 text-[11px] text-slate-500">
                {syncResult.applied} applied · {syncResult.duplicate} already done · {syncResult.rejected} rejected
              </p>
            )}
            <div className="max-h-64 overflow-y-auto">
              {syncQueue?.map(item => (
                <div key={item.client_mutation_id} className="border-b border-slate-50 px-4 py-2.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-700">{item.label}</span>
                    <span className="shrink-0 text-[10px] text-slate-400">{new Date(item.queued_at).toLocaleString()}</span>
                  </div>
                  {item.state === 'rejected' ? (
                    <div className="mt-1">
                      <p className="font-semibold text-red-600">{item.reason ?? 'Rejected by the server.'}</p>
                      <button
                        onClick={() => dismissQueued.mutate(item.client_mutation_id)}
                        className="mt-1 text-[11px] font-semibold text-slate-500 underline cursor-pointer"
                      >
                        Dismiss
                      </button>
                    </div>
                  ) : (
                    <p className="mt-1 text-slate-500">Waiting to sync…</p>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
