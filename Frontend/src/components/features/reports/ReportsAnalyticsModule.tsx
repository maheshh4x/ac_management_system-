'use client';

import React, { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { useACDataset } from '@/hooks/useACDataset';
import { calculateACUnitValue, useAssetFacets } from '@/lib/api/assets';
import { useMaintenanceTrend } from '@/lib/api/maintenance';
import {
  CSV_REPORTS,
  fetchReportCsv,
  reportFilename,
  type ReportFilters,
  type ReportName,
} from '@/lib/api/reports';
import { downloadBlob } from '@/lib/utils';
import { 
  Download, FileText, PieChart as PieIcon, Filter, 
  Wrench, Activity, Calendar, Zap, AlertTriangle, ZapOff, CheckCircle2, 
  Clock, IndianRupee, Building as BuildingIcon, HelpCircle, Layers
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart as RechartsPie, Pie, Cell, Legend
} from 'recharts';

const STATUS_COLORS: Record<string, string> = {
  Working: '#10B981',
  Fault: '#EF4444',
  Maintenance: '#F59E0B',
  Offline: '#94A3B8'
};

// Priority buckets from the maintenance-trend endpoint. The API emits the four
// PRIORITY_ORDER keys verbatim (reports.py: LOW/MEDIUM/HIGH/CRITICAL), so the
// map must be uppercase — the old `Low/Medium/High/Critical` keys never matched
// and every slice fell through to the grey default.
const PRIORITY_COLORS: Record<string, string> = {
  LOW: '#94A3B8',
  MEDIUM: '#3B82F6',
  HIGH: '#F97316',
  CRITICAL: '#EF4444',
};

/**
 * Calendar-safe `YYYY-MM-DD` arithmetic for the reporting-window pickers.
 * `reports.py::_trend_window` rejects a window wider than 24 months with
 * 400 INVALID_RANGE, so the `from`/`to` inputs constrain each other to the
 * widest window the endpoint accepts instead of surfacing that error.
 */
const TREND_WINDOW_MONTHS = 24;

function shiftMonths(isoDate: string, months: number): string {
  const [y, m, d] = isoDate.split('-').map(Number);
  if (!y || !m || !d) return '';
  const shifted = new Date(y, m - 1 + months, 1);
  const lastDay = new Date(shifted.getFullYear(), shifted.getMonth() + 1, 0).getDate();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(Math.min(d, lastDay))}`;
}

export function ReportsAnalyticsModule() {
  const [selectedBuilding, setSelectedBuilding] = useState<string>('All');
  const [selectedAcType, setSelectedAcType] = useState<string>('All');
  const [activeTab, setActiveTab] = useState<'status' | 'type' | 'building' | 'maintenance' | 'energy'>('status');
  const [searchTerm, setSearchTerm] = useState<string>('');
  // Reporting window (Phase 7 FE task 2): drives the maintenance-trend query
  // and every CSV download, so the chart and the file always agree.
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');
  const [csvReport, setCsvReport] = useState<ReportName>('due-for-maintenance');
  const [csvState, setCsvState] = useState<'idle' | 'working' | 'error'>('idle');
  const [csvError, setCsvError] = useState<string | null>(null);

  // Active filters object for live reactive query
  const filters = useMemo(() => ({
    buildingId: selectedBuilding,
    acType: selectedAcType,
    search: searchTerm
  }), [selectedBuilding, selectedAcType, searchTerm]);

  /**
   * `?from&to&block` for the report endpoints — shared by the trend query and
   * every CSV download. Block reuses the building filter so the chart, the
   * asset slice and the exported file all describe the same population.
   */
  const reportFilters = useMemo<ReportFilters>(() => ({
    from: dateFrom || undefined,
    to: dateTo || undefined,
    block: selectedBuilding === 'All' ? undefined : selectedBuilding,
  }), [dateFrom, dateTo, selectedBuilding]);

  // Real-time reactive data hooks (`all`: reports value the full slice)
  const { 
    acs, 
    stats, 
    statusDistribution, 
    typeDistribution, 
    buildingDistribution,
    totalAssetValue,
    avgAcAge,
    connectedPowerLoad,
    assetValueByBuilding,
    assetValueByType,
    faultFrequencyByBuilding,
  } = useACDataset(filters, { all: true });

  // Maintenance charts come straight from GET /reports/maintenance-trend
  // (zero-filled months, priority buckets, KPI stats — VIEW_REPORTS), scoped
  // to the selected reporting window and block.
  const { data: trend } = useMaintenanceTrend(reportFilters);
  const maintStats = trend?.stats;
  const monthlyTrend = trend?.monthlyTrend ?? [];
  const priorityDistribution = useMemo(
    () => (trend?.priorityDistribution ?? []).map(entry => ({ ...entry, color: PRIORITY_COLORS[entry.name] ?? '#64748B' })),
    [trend],
  );

  // Distinct building & type lists for dropdown filters (global facets —
  // stable regardless of the currently applied filter).
  const { data: facets } = useAssetFacets();

  const buildingOptions = useMemo(
    () => (facets?.block ?? []).map(b => b.value).sort(),
    [facets],
  );

  const typeOptions = useMemo(
    () => (facets?.asset_type ?? []).map(t => t.value).sort(),
    [facets],
  );

  // Operational Rate computation
  const operationalRate = useMemo(() => {
    if (!stats?.total) return 0;
    return Math.round((stats.working / stats.total) * 100);
  }, [stats]);

  // Current month string name
  const currentMonthName = useMemo(() => {
    return new Date().toLocaleString('default', { month: 'short', year: 'numeric' });
  }, []);

  // Anchor for the reporting-window `max` guard when only `from` is set.
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  // Export handlers
  /** Raw table → CSV of the rows already on screen (client-side by design:
   * these are the exact rows rendered below, not a re-query). */
  const handleExportVisibleRows = () => {
    if (acs.length === 0) return;
    const headers = ['AC ID', 'Brand', 'Model', 'Type', 'Capacity', 'Status', 'Building', 'Floor', 'Room', 'Installation Year', 'Est. Unit Price (INR)', 'Last Service Date'];
    const csvRows = [headers.join(',')];

    acs.forEach(ac => {
      const price = calculateACUnitValue(ac);
      csvRows.push([
        `"${ac.id}"`,
        `"${ac.make}"`,
        `"${ac.model}"`,
        `"${ac.type}"`,
        `"${ac.capacity}"`,
        `"${ac.status}"`,
        `"${ac.buildingName || ac.location.buildingId}"`,
        `"${ac.floorName || ac.location.floorId}"`,
        `"${ac.roomName || ac.location.roomId}"`,
        `"${ac.installationYear || ac.installationDate || 'N/A'}"`,
        price,
        `"${ac.lastMaintenanceDate || 'N/A'}"`
      ].join(','));
    });

    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    downloadBlob(blob, `NITTTR_AC_Asset_Report_${new Date().toISOString().split('T')[0]}.csv`);
  };

  /** Report → server CSV (`?format=csv`), filtered by the same window/block
   * the charts are showing. Authenticated, so it is fetched as a blob. */
  const handleDownloadReportCsv = async () => {
    setCsvState('working');
    setCsvError(null);
    try {
      const blob = await fetchReportCsv(csvReport, reportFilters);
      downloadBlob(blob, reportFilename(csvReport));
      setCsvState('idle');
    } catch (error) {
      setCsvState('error');
      setCsvError(error instanceof Error ? error.message : 'Download failed.');
    }
  };

  const handleExportPDF = () => {
    window.print();
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Header & Data Source Status Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-6 rounded-2xl border border-slate-200/80 shadow-xs">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Reports & Analytics</h1>
          </div>
          <p className="text-sm text-slate-500 mt-1">
            Real-time visual insights computed live from {stats?.total ?? acs.length} active AC records and {maintStats?.total ?? 0} maintenance jobs.
          </p>
        </div>

        <div data-print="hide" className="flex flex-col items-start gap-2 md:items-end">
          <div className="flex flex-wrap items-center gap-2">
            <button 
              onClick={handleExportPDF} 
              className="flex items-center gap-2 px-4 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-sm font-medium transition-colors cursor-pointer"
            >
              <FileText size={16} /> Export Report
            </button>
            <select
              value={csvReport}
              onChange={e => setCsvReport(e.target.value as ReportName)}
              aria-label="Report to download"
              className="px-3 py-2.5 text-xs font-medium bg-white border border-slate-200 rounded-xl text-slate-700 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
            >
              {CSV_REPORTS.map(report => (
                <option key={report.name} value={report.name}>{report.label}</option>
              ))}
            </select>
            <button 
              onClick={handleDownloadReportCsv} 
              disabled={csvState === 'working'}
              className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-medium shadow-xs transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Download size={16} /> {csvState === 'working' ? 'Downloading…' : 'Download CSV'}
            </button>
          </div>
          {csvState === 'error' && (
            <p className="text-xs text-red-600 md:text-right">{csvError}</p>
          )}
        </div>
      </div>

      {/* Global Filter Bar */}
      <div data-print="hide" className="flex flex-wrap items-center justify-between gap-4 bg-slate-50/80 p-4 rounded-xl border border-slate-200">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
            <Filter size={14} /> Filter Insights:
          </div>

          {/* Building Filter */}
          <select
            value={selectedBuilding}
            onChange={e => setSelectedBuilding(e.target.value)}
            className="px-3 py-2 text-xs font-medium bg-white border border-slate-200 rounded-lg text-slate-700 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
          >
            <option value="All">All Buildings ({buildingOptions.length})</option>
            {buildingOptions.map(b => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>

          {/* AC Type Filter */}
          <select
            value={selectedAcType}
            onChange={e => setSelectedAcType(e.target.value)}
            className="px-3 py-2 text-xs font-medium bg-white border border-slate-200 rounded-lg text-slate-700 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
          >
            <option value="All">All AC Types ({typeOptions.length})</option>
            {typeOptions.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>

          {/* Reporting window — ?from&to on the report endpoints */}
          <div className="flex items-center gap-1.5 border-l border-slate-300 pl-3">
            <Calendar size={14} className="text-slate-400" />
            <input
              type="date"
              aria-label="Report from date"
              value={dateFrom}
              max={shiftMonths(dateTo || todayIso, -(TREND_WINDOW_MONTHS - 1))}
              onChange={e => setDateFrom(e.target.value)}
              className="px-2 py-2 text-xs font-medium bg-white border border-slate-200 rounded-lg text-slate-700 outline-none focus:ring-2 focus:ring-blue-500"
            />
            <span className="text-xs text-slate-400">→</span>
            <input
              type="date"
              aria-label="Report to date"
              value={dateTo}
              min={dateFrom || undefined}
              max={dateFrom ? shiftMonths(dateFrom, TREND_WINDOW_MONTHS - 1) : undefined}
              onChange={e => setDateTo(e.target.value)}
              className="px-2 py-2 text-xs font-medium bg-white border border-slate-200 rounded-lg text-slate-700 outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {(selectedBuilding !== 'All' || selectedAcType !== 'All' || dateFrom || dateTo) && (
            <button
              onClick={() => { setSelectedBuilding('All'); setSelectedAcType('All'); setDateFrom(''); setDateTo(''); }}
              className="px-2.5 py-1 text-xs text-blue-600 hover:text-blue-800 font-medium underline"
            >
              Reset Filters
            </button>
          )}
        </div>

        <div className="flex flex-col items-end gap-0.5 text-xs text-slate-500 font-medium">
          <span>
            Showing <span className="font-bold text-slate-800">{stats?.total ?? acs.length}</span> units
          </span>
          <span className="text-[11px] text-slate-400">
            Date range scopes the trend chart and CSV exports; block/type scope everything.
          </span>
        </div>
      </div>

      {/* Top Live KPI Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* KPI 1: Total Asset Value */}
        <div className="bg-gradient-to-br from-blue-50/80 via-white to-blue-50/30 rounded-2xl border border-blue-100 p-5 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-blue-700 uppercase tracking-wide">Total Asset Value</span>
            <div className="p-2 bg-blue-100 text-blue-700 rounded-xl">
              <IndianRupee size={18} />
            </div>
          </div>
          <div className="mt-3">
            <p className="text-3xl font-extrabold text-blue-900 tracking-tight">{totalAssetValue.formattedValue}</p>
            <p className="text-xs text-slate-500 mt-1 flex items-center gap-1">
              <span>Dynamic valuation across {totalAssetValue.acsValuedCount} units</span>
            </p>
          </div>
        </div>

        {/* KPI 2: Operational Rate */}
        <div className="bg-gradient-to-br from-emerald-50/80 via-white to-emerald-50/30 rounded-2xl border border-emerald-100 p-5 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-emerald-700 uppercase tracking-wide">Operational Rate</span>
            <div className="p-2 bg-emerald-100 text-emerald-700 rounded-xl">
              <Activity size={18} />
            </div>
          </div>
          <div className="mt-3">
            <p className="text-3xl font-extrabold text-emerald-900 tracking-tight">{operationalRate}%</p>
            <p className="text-xs text-slate-500 mt-1">
              <span className="font-semibold text-emerald-700">{stats?.working ?? 0}</span> / {stats?.total ?? acs.length} units active & operational
            </p>
          </div>
        </div>

        {/* KPI 3: Avg AC Age */}
        <div className="bg-gradient-to-br from-purple-50/80 via-white to-purple-50/30 rounded-2xl border border-purple-100 p-5 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-purple-700 uppercase tracking-wide">Avg. AC Age</span>
            <div className="p-2 bg-purple-100 text-purple-700 rounded-xl">
              <Calendar size={18} />
            </div>
          </div>
          <div className="mt-3">
            <p className="text-3xl font-extrabold text-purple-900 tracking-tight">{avgAcAge.formattedAge}</p>
            <p className="text-xs text-slate-500 mt-1">
              Computed from install dates across {avgAcAge.unitsWithAgeCount} units
            </p>
          </div>
        </div>

        {/* KPI 4: MTD Maintenance Jobs */}
        <div className="bg-gradient-to-br from-amber-50/80 via-white to-amber-50/30 rounded-2xl border border-amber-100 p-5 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-amber-700 uppercase tracking-wide">MTD Maintenance</span>
            <div className="p-2 bg-amber-100 text-amber-700 rounded-xl">
              <Wrench size={18} />
            </div>
          </div>
          <div className="mt-3">
            <p className="text-3xl font-extrabold text-amber-900 tracking-tight">{maintStats?.mtdCount ?? 0} jobs</p>
            <p className="text-xs text-slate-500 mt-1">
              Recorded in <span className="font-semibold text-slate-700">{currentMonthName}</span>
            </p>
          </div>
        </div>
      </div>

      {/* Report Tab Selector */}
      <div className="flex gap-2 overflow-x-auto pb-1 bg-slate-100 p-1.5 rounded-2xl w-fit">
        {[
          { id: 'status', label: 'Status & Fault Distribution', icon: PieIcon },
          { id: 'type', label: 'AC Types & Valuation', icon: Layers },
          { id: 'building', label: 'By Building Analytics', icon: BuildingIcon },
          { id: 'maintenance', label: 'Maintenance Trend & MTTR', icon: Wrench },
          { id: 'energy', label: 'Connected Power & Energy', icon: Zap },
        ].map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold whitespace-nowrap transition-all cursor-pointer ${
                activeTab === tab.id
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-200/50'
              }`}
            >
              <Icon size={16} /> {tab.label}
            </button>
          );
        })}
      </div>

      {/* Main Interactive Charts Area */}
      <motion.div
        key={activeTab}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className="bg-white rounded-2xl border border-slate-200/80 shadow-xs p-6"
      >
        {/* TAB 1: STATUS OVERVIEW & FAULT RISK */}
        {activeTab === 'status' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">AC Unit Status Distribution</h3>
              <p className="text-xs text-slate-500 mb-6">Live proportion of working, faulty, maintenance, and offline units.</p>
              
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <RechartsPie>
                    <Pie
                      data={statusDistribution}
                      cx="50%"
                      cy="50%"
                      outerRadius={100}
                      innerRadius={65}
                      paddingAngle={5}
                      dataKey="value"
                      label={({ name, percent = 0 }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
                    >
                      {statusDistribution.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color || STATUS_COLORS[entry.name] || '#94A3B8'} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                    <Legend iconType="circle" />
                  </RechartsPie>
                </ResponsiveContainer>
              </div>
            </div>

            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">Building Fault Frequency & Downtime Risk</h3>
              <p className="text-xs text-slate-500 mb-6">Count of units currently Faulty or under Maintenance per building.</p>

              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={faultFrequencyByBuilding} layout="vertical" margin={{ left: 10, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                    <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
                    <YAxis dataKey="building" type="category" axisLine={false} tickLine={false} width={120} tick={{ fontSize: 11, fill: '#475569' }} />
                    <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                    <Bar dataKey="faultyCount" name="Faulty / Maint. Units" fill="#EF4444" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: AC TYPES & VALUATION */}
        {activeTab === 'type' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">AC Units by Type</h3>
              <p className="text-xs text-slate-500 mb-6">Live inventory breakdown across AC model types.</p>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={typeDistribution} margin={{ left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                    <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
                    <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} allowDecimals={false} />
                    <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                    <Bar dataKey="value" name="Total Units" radius={[6, 6, 0, 0]}>
                      {typeDistribution.map((_, i) => (
                        <Cell key={i} fill={['#3B82F6', '#8B5CF6', '#06B6D4', '#F43F5E', '#10B981'][i % 5]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">Estimated Asset Value by AC Type</h3>
              <p className="text-xs text-slate-500 mb-6">Total financial value aggregated per AC type (in ₹ Lakhs).</p>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={assetValueByType} margin={{ left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                    <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
                    <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} tickFormatter={v => `₹${(v/100000).toFixed(1)}L`} />
                    <Tooltip 
                      formatter={(val: any) => [`₹${(Number(val)/100000).toFixed(2)} Lakhs`, 'Asset Value']}
                      contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} 
                    />
                    <Bar dataKey="totalValue" name="Total Asset Value" fill="#6366F1" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: BY BUILDING ANALYTICS */}
        {activeTab === 'building' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">AC Units per Building</h3>
              <p className="text-xs text-slate-500 mb-6">Asset density across college blocks and departments.</p>
              <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={buildingDistribution} layout="vertical" margin={{ left: 10, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                    <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} allowDecimals={false} />
                    <YAxis dataKey="building" type="category" axisLine={false} tickLine={false} width={130} tick={{ fontSize: 11, fill: '#475569' }} />
                    <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                    <Bar dataKey="count" name="AC Units" fill="#0284C7" radius={[0, 6, 6, 0]}>
                      {buildingDistribution.map((_, i) => (
                        <Cell key={i} fill={`hsl(${210 + i * 22}, 75%, ${55 - i * 2}%)`} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div>
              <h3 className="text-base font-bold text-slate-900 mb-1">Asset Valuation per Building</h3>
              <p className="text-xs text-slate-500 mb-6">Estimated monetary value of cooling equipment in each building.</p>
              <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={assetValueByBuilding} layout="vertical" margin={{ left: 10, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                    <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#64748B' }} tickFormatter={v => `₹${(v/100000).toFixed(0)}L`} />
                    <YAxis dataKey="building" type="category" axisLine={false} tickLine={false} width={130} tick={{ fontSize: 11, fill: '#475569' }} />
                    <Tooltip 
                      formatter={(val: any) => [`₹${(Number(val)/100000).toFixed(2)} Lakhs`, 'Total Value']}
                      contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} 
                    />
                    <Bar dataKey="totalValue" name="Asset Value" fill="#059669" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}

        {/* TAB 4: MAINTENANCE TREND & MTTR */}
        {activeTab === 'maintenance' && (
          <div className="flex flex-col gap-8">
            {/* KPI Banner */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 bg-slate-50 p-4 rounded-xl border border-slate-200">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-blue-100 text-blue-700 rounded-lg">
                  <Clock size={20} />
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase">Mean Time To Resolve (MTTR)</p>
                  <p className="text-xl font-extrabold text-slate-900">{maintStats?.mttrDays ?? 0} days</p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-amber-100 text-amber-700 rounded-lg">
                  <AlertTriangle size={20} />
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase">Overdue Maintenance</p>
                  <p className="text-xl font-extrabold text-amber-700">{maintStats?.overdueCount ?? 0} jobs</p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-emerald-100 text-emerald-700 rounded-lg">
                  <CheckCircle2 size={20} />
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase">Upcoming Scheduled</p>
                  <p className="text-xl font-extrabold text-emerald-700">{maintStats?.upcomingCount ?? 0} jobs</p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              <div className="lg:col-span-2">
                <h3 className="text-base font-bold text-slate-900 mb-1">Maintenance Jobs – Monthly Trend</h3>
                <p className="text-xs text-slate-500 mb-6">Aggregated live job counts by maintenance category over time.</p>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={monthlyTrend} margin={{ left: -10 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                      <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} allowDecimals={false} />
                      <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                      <Legend />
                      <Bar dataKey="Preventive" fill="#2563EB" radius={[4, 4, 0, 0]} stackId="a" />
                      <Bar dataKey="Repair" fill="#F59E0B" radius={[4, 4, 0, 0]} stackId="a" />
                      <Bar dataKey="Emergency" fill="#EF4444" radius={[4, 4, 0, 0]} stackId="a" />
                      <Bar dataKey="Inspection" fill="#10B981" radius={[4, 4, 0, 0]} stackId="a" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div>
                <h3 className="text-base font-bold text-slate-900 mb-1">Jobs by Priority</h3>
                <p className="text-xs text-slate-500 mb-6">Breakdown of reported jobs by urgency.</p>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <RechartsPie>
                      <Pie
                        data={priorityDistribution}
                        cx="50%"
                        cy="50%"
                        outerRadius={90}
                        innerRadius={55}
                        paddingAngle={4}
                        dataKey="value"
                        label={({ name, value }) => `${name}: ${value}`}
                      >
                        {priorityDistribution.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }} />
                    </RechartsPie>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 5: ELECTRICAL POWER & ENERGY */}
        {activeTab === 'energy' && (
          <div className="flex flex-col gap-6">
            <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 bg-gradient-to-r from-amber-500/10 via-amber-500/5 to-transparent p-5 rounded-2xl border border-amber-200">
              <div className="flex items-center gap-4">
                <div className="p-3 bg-amber-500 text-white rounded-xl shadow-xs">
                  <Zap size={24} />
                </div>
                <div>
                  <h4 className="font-bold text-slate-900 text-base">Total Connected Power Load</h4>
                  <p className="text-xs text-slate-600 mt-0.5">Sum of electrical power ratings across all operating units</p>
                </div>
              </div>

              <div className="text-right">
                <span className="text-3xl font-extrabold text-amber-900 tracking-tight">{connectedPowerLoad.formattedKw}</span>
                <p className="text-xs text-slate-500">Across {connectedPowerLoad.unitsWithPowerCount} units</p>
              </div>
            </div>

            {/* Informative Guidance Banner for Missing Telemetry */}
            <div className="bg-slate-50 border border-slate-200 rounded-2xl p-6 flex flex-col md:flex-row gap-5 items-start">
              <div className="p-3 bg-slate-200 text-slate-600 rounded-xl flex-shrink-0 mt-1">
                <ZapOff size={24} />
              </div>

              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="font-bold text-slate-800 text-sm">Granular Real-Time IoT Power Consumption (kWh)</h4>
                  <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-100 text-amber-800 border border-amber-200">
                    Telemetry Not Connected
                  </span>
                </div>
                <p className="text-xs text-slate-600 mt-1.5 leading-relaxed">
                  Real-time kWh electrical consumption per unit and live monthly power utility billing are not currently captured in the database schema.
                </p>
                
                <div className="mt-4 bg-white p-4 rounded-xl border border-slate-200/80">
                  <h5 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <HelpCircle size={14} className="text-blue-600" /> Recommendations to Enable Live Energy Telemetry:
                  </h5>
                  <ul className="text-xs text-slate-600 space-y-1.5 list-disc pl-4">
                    <li>Install IoT Current Transformers / Smart Energy Clamp Meters on AC power supply lines.</li>
                    <li>Configure MQTT or HTTP Webhook telemetry endpoint to ingest live voltage, current, and kWh usage into the database.</li>
                    <li>Add <code className="bg-slate-100 px-1 py-0.5 rounded text-blue-600 font-mono">energyUsageKwh</code> and <code className="bg-slate-100 px-1 py-0.5 rounded text-blue-600 font-mono">monthlyCostINR</code> fields to the AC unit schema.</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}
      </motion.div>

      {/* Raw Asset & Maintenance Data Table */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-xs overflow-hidden">
        <div className="p-5 border-b border-slate-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h3 className="font-bold text-slate-900 text-base">Raw Asset & Valuation Records</h3>
            <p className="text-xs text-slate-500 mt-0.5">Live tabular view of processed asset units.</p>
          </div>

          <div data-print="hide" className="flex items-center gap-3 w-full sm:w-auto">
            <input
              type="text"
              placeholder="Search ID, brand, location..."
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              className="px-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg outline-none focus:ring-2 focus:ring-blue-500 w-full sm:w-56"
            />
            <button 
              onClick={handleExportVisibleRows}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold transition-colors flex-shrink-0 cursor-pointer"
            >
              <Download size={13} /> Export CSV
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-xs">
            <thead className="bg-slate-50 border-b border-slate-100">
              <tr>
                {['AC ID', 'Brand & Model', 'Type', 'Capacity', 'Status', 'Building & Room', 'Install Year', 'Est. Value', 'Last Service'].map(h => (
                  <th key={h} className="text-left font-bold text-slate-600 px-4 py-3 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {acs.map(ac => {
                const estValue = calculateACUnitValue(ac);
                return (
                  <tr key={ac.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-4 py-3 font-mono font-bold text-blue-700">{ac.id}</td>
                    <td className="px-4 py-3 text-slate-800 font-medium">{ac.make} {ac.model}</td>
                    <td className="px-4 py-3 text-slate-600">{ac.type}</td>
                    <td className="px-4 py-3 text-slate-600 font-semibold">{ac.capacity}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${
                        ac.status === 'Working' ? 'bg-emerald-100 text-emerald-800 border-emerald-200' :
                        ac.status === 'Fault' ? 'bg-red-100 text-red-800 border-red-200' :
                        ac.status === 'Maintenance' ? 'bg-amber-100 text-amber-800 border-amber-200' :
                        'bg-slate-100 text-slate-600 border-slate-200'
                      }`}>
                        {ac.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {ac.buildingName || ac.location.buildingId} {ac.roomName ? `• ${ac.roomName}` : ''}
                    </td>
                    <td className="px-4 py-3 text-slate-600 font-mono">{ac.installationYear || ac.installationDate || 'N/A'}</td>
                    <td className="px-4 py-3 text-slate-900 font-bold">₹{estValue.toLocaleString()}</td>
                    <td className="px-4 py-3 text-slate-500">{ac.lastMaintenanceDate || 'N/A'}</td>
                  </tr>
                );
              })}

              {acs.length === 0 && (
                <tr>
                  <td colSpan={9} className="text-center py-10 text-slate-400">
                    No AC asset records found matching current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
