import React from 'react';
import { cn } from '@/lib/utils';
import { AcStatus } from '@/lib/types/campus';

export function StatusBadge({ status, className }: { status: AcStatus | string; className?: string }) {
  let indicatorColor = '';
  let badgeColor = '';
  let textColor = '';

  switch (status) {
    case 'Working':
      indicatorColor = 'bg-success';
      badgeColor = 'bg-success/10';
      textColor = 'text-success';
      break;
    case 'Fault':
      indicatorColor = 'bg-danger animate-pulse';
      badgeColor = 'bg-danger/10';
      textColor = 'text-danger';
      break;
    case 'Maintenance':
      indicatorColor = 'bg-warning animate-pulse';
      badgeColor = 'bg-warning/10';
      textColor = 'text-warning';
      break;
    default:
      indicatorColor = 'bg-slate-400';
      badgeColor = 'bg-slate-100';
      textColor = 'text-slate-600';
  }

  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-transparent", badgeColor, textColor, className)}>
      <span className={cn("w-1.5 h-1.5 rounded-full inline-block", indicatorColor)}></span>
      {status}
    </span>
  );
}
