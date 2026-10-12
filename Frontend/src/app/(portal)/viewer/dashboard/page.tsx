'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { KpiCard } from '@/components/ui/KpiCard';
import { useDashboardSummary } from '@/lib/api/assets';
import { Airplay, CheckCircle2, ShieldCheck, Map } from 'lucide-react';
import Link from 'next/link';

export default function ViewerDashboardPage() {
  const { data: summary, isLoading } = useDashboardSummary({});
  const kpis = summary?.kpis;
  const totalAcs = kpis?.total;
  const workingAcs = kpis?.working;

  return (
    <div className="flex flex-col gap-8 pb-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Observer Dashboard</h1>
        <p className="text-text-secondary text-sm">Read-only view of AC assets and campus infrastructure.</p>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, staggerChildren: 0.1 }}
        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
      >
        <KpiCard
          title="Total ACs"
          value={isLoading ? '…' : (totalAcs ?? '—')}
          icon={Airplay}
          color="brand"
          trendPositive={true}
        />
        <KpiCard
          title="Working ACs"
          value={isLoading ? '…' : (workingAcs ?? '—')}
          icon={CheckCircle2}
          color="success"
          trendPositive={true}
        />
        <KpiCard title="Role Access" value="Viewer" icon={ShieldCheck} color="warning" trend="Read-only" trendPositive={false} />
      </motion.div>

      <div className="bg-blue-50 border border-blue-100 p-6 rounded-xl flex items-center justify-between">
         <div className="max-w-xl">
           <h3 className="text-lg font-bold text-blue-900">Explore 3D Campus map</h3>
           <p className="text-blue-700 text-sm mt-1">Navigate visually through all departments and blocks to see where each AC is installed.</p>
         </div>
         <Link href="/campus" className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg shadow-sm transition-colors flex items-center gap-2">
           <Map size={18} /> Open 3D Map
         </Link>
      </div>
    </div>
  );
}
