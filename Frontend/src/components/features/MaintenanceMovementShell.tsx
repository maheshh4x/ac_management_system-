'use client';

import React, { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { cn } from '@/lib/utils';

interface MaintenanceMovementShellProps {
  maintenance: ReactNode;
  movement: ReactNode;
}

export function MaintenanceMovementShell({ maintenance, movement }: MaintenanceMovementShellProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeTab = searchParams.get('tab') === 'movement' ? 'movement' : 'maintenance';

  return (
    <div className="flex flex-col gap-6">
      <div className="inline-flex w-fit rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
        {[
          { id: 'maintenance' as const, label: 'Maintenance' },
          { id: 'movement' as const, label: 'Movement' },
        ].map(tab => (
          <Link
            key={tab.id}
            href={`${pathname}?tab=${tab.id}`}
            className={cn(
              'rounded-md px-4 py-2 text-sm font-semibold transition-colors',
              activeTab === tab.id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
            )}
          >
            {tab.label}
          </Link>
        ))}
      </div>
      {activeTab === 'movement' ? movement : maintenance}
    </div>
  );
}