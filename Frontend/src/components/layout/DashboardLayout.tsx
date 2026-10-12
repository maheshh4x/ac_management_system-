'use client';

import React, { useState } from 'react';
import { Sidebar } from './Sidebar';
import { TopNavbar } from './TopNavbar';
import { AssistantWidget } from './AssistantWidget';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import { Airplay, History, LayoutDashboard, Map, UserRound, Wrench, type LucideIcon } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();
  const { user } = useAuth();
  
  // Do not wrap Login or 403 in this layout
  if (pathname === '/login' || pathname === '/403') {
    return <>{children}</>;
  }

  return (
    <div data-print="page" className="flex h-screen bg-background text-text-primary overflow-hidden">
      <Sidebar collapsed={collapsed} setCollapsed={setCollapsed} />
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <TopNavbar />
        <main data-print="main" className="flex-1 overflow-x-hidden overflow-y-auto bg-background p-4 pb-24 md:p-6 md:pb-6">
          <div className="max-w-7xl mx-auto h-full">
            {children}
          </div>
        </main>
      </div>
      {user?.role === 'TECHNICIAN' && <nav data-print="hide" className="fixed bottom-0 left-0 right-0 z-40 grid grid-cols-6 border-t border-slate-200 bg-white/95 shadow-[0_-4px_16px_rgba(15,23,42,0.08)] backdrop-blur md:hidden">{([
        [LayoutDashboard, 'Dashboard', '/technician/dashboard'], [Wrench, 'Jobs', '/technician/jobs'], [Airplay, 'Scan', '/technician/scan'], [Map, 'Locations', '/technician/campus'], [History, 'Moves', '/technician/movement'], [UserRound, 'Profile', '/technician/profile'],
      ] as Array<[LucideIcon, string, string]>).map(([Icon, label, href]) => <Link key={href} href={href} className={`flex min-h-16 flex-col items-center justify-center gap-1 text-[10px] font-semibold ${pathname.startsWith(href) ? 'text-blue-600' : 'text-slate-500'}`}><Icon size={18} />{label}</Link>)}</nav>}
      <AssistantWidget />
    </div>
  );
}
