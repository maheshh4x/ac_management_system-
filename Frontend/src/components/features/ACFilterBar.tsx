// src/components/features/ACFilterBar.tsx
'use client';

import React from 'react';
import { Search, Filter, X, RotateCcw } from 'lucide-react';
import { ACFilters } from '@/lib/types/asset';

export interface BuildingOption {
  id: string;
  name: string;
  count?: number;
}

interface ACFilterBarProps {
  filters: ACFilters;
  onChange: (newFilters: ACFilters) => void;
  onReset?: () => void;
  /** Facet-derived block options; empty until the facets query resolves. */
  buildingList?: BuildingOption[];
}

const AC_TYPES = ['All', 'Split', 'Window', 'Cassette', 'Ductable', 'Central'];
/** The complete status-filter vocabulary; anything else is not a filter. */
export const STATUSES = ['All', 'Working', 'Fault', 'Maintenance', 'Offline'];

export const ACFilterBar: React.FC<ACFilterBarProps> = ({
  filters,
  onChange,
  onReset,
  buildingList = []
}) => {
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange({ ...filters, search: e.target.value });
  };

  const handleBuildingChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onChange({ ...filters, buildingId: e.target.value });
  };

  const handleTypeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onChange({ ...filters, acType: e.target.value });
  };

  const handleStatusChange = (status: string) => {
    onChange({ ...filters, status });
  };

  const hasActiveFilters = 
    (filters.search && filters.search.trim() !== '') ||
    (filters.buildingId && filters.buildingId !== 'All') ||
    (filters.acType && filters.acType !== 'All') ||
    (filters.status && filters.status !== 'All');

  const handleClearAll = () => {
    if (onReset) {
      onReset();
    } else {
      onChange({ search: '', buildingId: 'All', acType: 'All', status: 'All' });
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-2xs space-y-3">
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
        {/* Search Bar */}
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search by AC ID, Serial No, Building, Room, Make..."
            value={filters.search || ''}
            onChange={handleSearchChange}
            className="w-full pl-10 pr-9 py-2 border border-slate-200 rounded-lg text-sm bg-slate-50/50 focus:bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition"
          />
          {filters.search && (
            <button
              onClick={() => onChange({ ...filters, search: '' })}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 p-1"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Building Filter */}
        <div className="flex items-center gap-2">
          <select
            value={filters.buildingId || 'All'}
            onChange={handleBuildingChange}
            className="px-3 py-2 border border-slate-200 rounded-lg text-xs font-medium bg-white text-slate-700 focus:ring-2 focus:ring-blue-500 outline-none"
          >
            <option value="All">All Buildings</option>
            {buildingList.map(b => (
              <option key={b.id} value={b.id}>
                {b.name} ({b.id})
              </option>
            ))}
          </select>

          {/* AC Type Filter */}
          <select
            value={filters.acType || 'All'}
            onChange={handleTypeChange}
            className="px-3 py-2 border border-slate-200 rounded-lg text-xs font-medium bg-white text-slate-700 focus:ring-2 focus:ring-blue-500 outline-none"
          >
            <option value="All">All AC Types</option>
            {AC_TYPES.filter(t => t !== 'All').map(type => (
              <option key={type} value={type}>
                {type} AC
              </option>
            ))}
          </select>

          {hasActiveFilters && (
            <button
              onClick={handleClearAll}
              className="flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 transition-colors"
            >
              <RotateCcw size={13} /> Reset
            </button>
          )}
        </div>
      </div>

      {/* Status Chips */}
      <div className="flex items-center gap-2 pt-1 border-t border-slate-100 overflow-x-auto">
        <span className="text-[11px] font-semibold uppercase text-slate-400 mr-1 flex items-center gap-1">
          <Filter size={11} /> Status:
        </span>
        {STATUSES.map(s => {
          const isActive = (filters.status || 'All') === s;
          return (
            <button
              key={s}
              onClick={() => handleStatusChange(s)}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition-all whitespace-nowrap ${
                isActive
                  ? 'bg-slate-900 text-white border-slate-900 shadow-2xs'
                  : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              {s}
            </button>
          );
        })}
      </div>
    </div>
  );
};
