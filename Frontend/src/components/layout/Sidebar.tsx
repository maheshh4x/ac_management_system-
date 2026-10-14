'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Airplay, MapPin, Wrench, History, FileText, Settings, ShieldCheck, ChevronLeft, ChevronRight, Map, Users, FileLock, ClipboardCheck, ClipboardList, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { motion } from 'framer-motion';
import { useAuth } from '@/contexts/AuthContext';
import { hasPermission, Permission } from '@/lib/permissions';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { useApprovals } from '@/lib/api/users';
import { usePendingReports } from '@/lib/api/issue-reports';

interface SidebarProps {
  collapsed: boolean;
  setCollapsed: (val: boolean) => void;
}

interface NavItem {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  href: string;
  permission: Permission | null;
  badge?: number;
}

export function Sidebar({ collapsed, setCollapsed }: SidebarProps) {
  const pathname = usePathname();
  const { user } = useAuth();
  // Pending-approval badge now counts real rows from GET /approvals (Phase 7
  // retired the localStorage mock store that used to back this number).
  const approvals = useApprovals('PENDING', {
    enabled: hasPermission(user?.role || null, 'MANAGE_USERS'),
  });
  const pendingCount = approvals.data?.pagination.total ?? 0;
  // Issue-report queue badge (SUPER_ADMIN only — the only role that sees
  // the approval UI; the backend scopes the query regardless).
  const isAdmin = user?.role === 'SUPER_ADMIN';
  const issuePending = usePendingReports(isAdmin);
  const issuePendingCount = issuePending.data?.pagination.total ?? 0;
  
  const roleBase = user?.role === 'SUPER_ADMIN' ? '/admin' : 
                   user?.role === 'FACILITY_MANAGER' ? '/manager' : 
                   user?.role === 'TECHNICIAN' ? '/technician' : '/viewer';

  const getNavItems = (): NavItem[] => {
    if (user?.role === 'TECHNICIAN') {
       return [
         { icon: LayoutDashboard, label: 'Dashboard', href: `${roleBase}/dashboard`, permission: null },
         { icon: Zap, label: 'Energy Management', href: `${roleBase}/energy`, permission: 'VIEW_AC' },
         { icon: Wrench, label: 'My Jobs', href: `${roleBase}/jobs`, permission: null },
         { icon: ClipboardList, label: 'My Reports', href: `${roleBase}/my-reports`, permission: null },
         { icon: Airplay, label: 'Scan QR', href: `${roleBase}/scan`, permission: null },
         { icon: Map, label: 'AC Locations', href: '/campus', permission: null },
         { icon: History, label: 'Movement Requests', href: `${roleBase}/movement`, permission: null },
         { icon: Users, label: 'My Profile', href: `${roleBase}/profile`, permission: null },
       ]
    }

    if (user?.role === 'VIEWER') {
       return [
         { icon: LayoutDashboard, label: 'Dashboard', href: `${roleBase}/dashboard`, permission: null },
         { icon: Airplay, label: 'AC Assets', href: `${roleBase}/assets`, permission: 'VIEW_AC' },
         { icon: Wrench, label: 'Maintenance & Movement', href: '/manager/maintenance', permission: null },
         { icon: ClipboardList, label: 'My Reports', href: `${roleBase}/my-reports`, permission: null },
         { icon: Zap, label: 'Energy Management', href: `${roleBase}/energy`, permission: 'VIEW_AC' },
         { icon: Users, label: 'Users & Roles', href: '/admin/users', permission: null },
         { icon: ClipboardCheck, label: 'Pending Approvals', href: '/admin/approvals', permission: null, badge: pendingCount },
         { icon: Map, label: 'AC Search', href: `${roleBase}/search`, permission: null },
         { icon: MapPin, label: 'Campus Map', href: '/campus', permission: null },
         { icon: Users, label: 'My Profile', href: `${roleBase}/profile`, permission: null },
       ]
    }

    // Admins and Managers
    return [
      { icon: LayoutDashboard, label: 'Dashboard', href: `${roleBase}/dashboard`, permission: null },
      { icon: ClipboardCheck, label: 'Pending Approvals', href: `${roleBase}/approvals`, permission: null, badge: pendingCount + (isAdmin ? issuePendingCount : 0) },
      { icon: MapPin, label: 'Campus Mgmt', href: `${roleBase}/campus`, permission: 'MANAGE_CAMPUS' },
      { icon: Map, label: '3D Campus Map', href: '/campus', permission: null },
      { icon: Airplay, label: 'AC Assets', href: `${roleBase}/assets`, permission: 'VIEW_AC' },
      { icon: Wrench, label: 'Maintenance & Movement', href: `${roleBase}/maintenance`, permission: 'VIEW_AC' },
      { icon: FileText, label: 'Asset Value', href: `${roleBase}/asset-value`, permission: 'VIEW_REPORTS' },
      { icon: ClipboardList, label: 'My Reports', href: `${roleBase}/my-reports`, permission: null },
      { icon: Zap, label: 'Energy Management', href: `${roleBase}/energy`, permission: 'VIEW_AC' },
      { icon: Users, label: 'Users & Roles', href: `${roleBase}/users`, permission: 'MANAGE_USERS' },
      { icon: FileLock, label: 'Audit Logs', href: `${roleBase}/audit`, permission: 'VIEW_AUDIT_LOGS' },
    ];
  };

  const navItems = getNavItems().filter(item => !item.permission || hasPermission(user?.role || null, item.permission));

  return (
    <motion.aside
      initial={{ width: 260 }}
      animate={{ width: collapsed ? 80 : 260 }}
      transition={{ type: 'spring', damping: 20, stiffness: 200 }}
      data-print="hide"
      className="hidden h-full bg-slate-900 flex-col relative z-20 shadow-xl text-white border-r border-slate-800 md:flex"
    >
      <div className="p-4 flex items-center justify-between border-b border-slate-800 h-16">
        <div className={cn("flex items-center gap-3 overflow-hidden", collapsed && "justify-center w-full")}>
          <div className="min-w-8 min-h-8 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center backdrop-blur-md">
            <ShieldCheck size={20} />
          </div>
          {!collapsed && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="whitespace-nowrap font-medium text-white">
              <span className="font-bold text-blue-400">NITTTR</span> AC Portal
            </motion.div>
          )}
        </div>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="absolute -right-3 top-5 bg-slate-800 border border-slate-700 rounded-full p-1 text-slate-400 hover:text-white hover:bg-blue-600 transition-colors shadow-md"
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-4 flex flex-col gap-1.5 px-3">
        {navItems.map((item) => {
          const isActive = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href) && item.href.length > 2);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all group overflow-hidden whitespace-nowrap",
                isActive 
                  ? "bg-blue-600 text-white font-semibold shadow-sm" 
                  : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              )}
            >
              <item.icon size={18} className={cn("min-w-5 transition-transform group-hover:scale-110", isActive ? "text-white" : "text-slate-400")} />
              {!collapsed && (
                <span className="text-sm flex items-center justify-between gap-2 w-full">
                  {item.label}
                  {!!item.badge && <span className="min-w-5 h-5 px-1 rounded-full bg-amber-400 text-slate-950 text-[11px] font-bold flex items-center justify-center">{item.badge}</span>}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      <ProtectedAction permission="MANAGE_SETTINGS">
        <div className="p-3 border-t border-slate-800">
          <Link
            href="/settings"
            className={cn(
              "flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors group overflow-hidden whitespace-nowrap",
              pathname.startsWith('/settings') 
                ? "bg-blue-600 text-white font-semibold shadow-sm" 
                : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            )}
          >
            <Settings size={18} className={cn("min-w-5 transition-transform group-hover:scale-110", pathname.startsWith('/settings') ? "text-white" : "text-slate-400")} />
            {!collapsed && <span className="text-sm">Settings</span>}
          </Link>
        </div>
      </ProtectedAction>
    </motion.aside>
  );
}
