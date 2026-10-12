'use client';

import React, { useState } from 'react';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

interface Column<T> {
  header: string;
  accessor: keyof T | ((item: T) => React.ReactNode);
  className?: string;
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  keyExtractor: (item: T) => string;
  onRowClick?: (item: T) => void;
  searchPlaceholder?: string;
  onSearch?: (term: string) => void;
}

export function DataTable<T>({ data, columns, keyExtractor, onRowClick, searchPlaceholder, onSearch }: DataTableProps<T>) {
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  
  const totalPages = Math.ceil(data.length / itemsPerPage);
  const startIdx = (currentPage - 1) * itemsPerPage;
  const currentData = data.slice(startIdx, startIdx + itemsPerPage);

  return (
    <div className="bg-card rounded-xl border border-card-border overflow-hidden shadow-sm flex flex-col">
      {/* Controls */}
      <div className="p-4 border-b border-card-border flex flex-col sm:flex-row justify-between items-center gap-4">
        <div className="relative w-full sm:w-80">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
          <input
            type="text"
            placeholder={searchPlaceholder || "Search..."}
            onChange={(e) => onSearch?.(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-card-border rounded-lg text-sm focus:outline-none focus:border-brand focus:ring-1 focus:ring-brand transition-all"
          />
        </div>
        {/* Additional filters can go here */}
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead className="text-xs text-text-secondary uppercase bg-slate-50/50 border-b border-card-border">
            <tr>
              {columns.map((col, idx) => (
                <th key={idx} className={cn("px-6 py-4 font-semibold text-slate-500 tracking-wider", col.className)}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {currentData.length > 0 ? (
                currentData.map((item, idx) => (
                  <motion.tr
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2, delay: idx * 0.02 }}
                    key={keyExtractor(item)}
                    onClick={() => onRowClick && onRowClick(item)}
                    className={cn(
                      "border-b border-card-border last:border-0 hover:bg-slate-50 transition-colors",
                      onRowClick && "cursor-pointer"
                    )}
                  >
                    {columns.map((col, colIdx) => (
                      <td key={colIdx} className={cn("px-6 py-4 whitespace-nowrap", col.className)}>
                        {typeof col.accessor === 'function' ? col.accessor(item) : (item[col.accessor] as React.ReactNode)}
                      </td>
                    ))}
                  </motion.tr>
                ))
              ) : (
                <tr>
                  <td colSpan={columns.length} className="px-6 py-12 text-center text-text-secondary">
                    No results found
                  </td>
                </tr>
              )}
            </AnimatePresence>
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="p-4 border-t border-card-border flex items-center justify-between bg-slate-50/30">
        <span className="text-sm text-text-secondary">
          Showing <span className="font-medium text-text-primary">{data.length > 0 ? startIdx + 1 : 0}</span> to <span className="font-medium text-text-primary">{Math.min(startIdx + itemsPerPage, data.length)}</span> of <span className="font-medium text-text-primary">{data.length}</span> results
        </span>
        
        <div className="flex gap-2">
          <button 
            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            disabled={currentPage === 1}
            className="p-2 border border-card-border rounded hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed text-text-secondary transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <button 
            onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
            disabled={currentPage === totalPages || totalPages === 0}
            className="p-2 border border-card-border rounded hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed text-text-secondary transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
