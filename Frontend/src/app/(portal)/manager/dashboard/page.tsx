'use client';

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useACDataset } from '@/hooks/useACDataset';
import { ACStatCards } from '@/components/features/ACStatCards';
import { ACStatusChart } from '@/components/features/charts/ACStatusChart';
import { ACTypeChart } from '@/components/features/charts/ACTypeChart';
import { ImportACModal } from '@/components/features/csv-import/ImportACModal';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { Upload } from 'lucide-react';

export default function ManagerDashboardPage() {
  const [showImportModal, setShowImportModal] = useState(false);
  const {
    stats,
    loading,
    error,
    refreshData,
    statusDistribution,
    typeDistribution,
  } = useACDataset();

  return (
    <div className="flex flex-col gap-8 pb-8">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Facility Manager Command Interface</h1>
          <p className="text-slate-500 text-sm mt-0.5">Monitor AC asset health and maintenance schedules across campus.</p>
        </div>

        <ProtectedAction permission="CREATE_AC">
          <button
            onClick={() => setShowImportModal(true)}
            className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs font-semibold shadow-2xs transition-colors"
          >
            <Upload size={15} /> Import College Data
          </button>
        </ProtectedAction>
      </div>

      <ACStatCards
        stats={stats}
        loading={loading}
        error={error}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="bg-white rounded-xl border border-slate-200 p-6 shadow-2xs"
        >
          <h3 className="text-sm font-bold text-slate-900 mb-4">AC Status Overview</h3>
          <ACStatusChart
            data={statusDistribution}
            loading={loading}
            error={error}
            onRetry={refreshData}
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className="bg-white rounded-xl border border-slate-200 p-6 shadow-2xs"
        >
          <h3 className="text-sm font-bold text-slate-900 mb-4">AC Type Distribution</h3>
          <ACTypeChart
            data={typeDistribution}
            loading={loading}
            error={error}
            onRetry={refreshData}
          />
        </motion.div>
      </div>

      <ImportACModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        onImported={refreshData}
      />
    </div>
  );
}
