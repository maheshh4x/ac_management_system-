'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { LucideIcon } from 'lucide-react';

interface KpiCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  trend?: string;
  trendPositive?: boolean;
  color?: 'brand' | 'success' | 'warning' | 'danger';
}

export function KpiCard({ title, value, icon: Icon, trend, trendPositive, color = 'brand' }: KpiCardProps) {
  
  const colorStyles = {
    brand: 'text-brand bg-brand/10',
    success: 'text-success bg-success/10',
    warning: 'text-warning bg-warning/10',
    danger: 'text-danger bg-danger/10'
  };

  return (
    <motion.div
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="bg-card rounded-xl p-5 border border-card-border shadow-sm flex flex-col justify-between"
    >
      <div className="flex justify-between items-start mb-4">
        <p className="text-sm font-medium text-text-secondary">{title}</p>
        <div className={cn("p-2 rounded-lg flex items-center justify-center", colorStyles[color])}>
          <Icon size={18} />
        </div>
      </div>
      
      <div>
        <h3 className="text-3xl font-bold text-text-primary tracking-tight">
          {typeof value === 'number' ? (
            <motion.span
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
            >
              {value}
            </motion.span>
          ) : (
            value
          )}
        </h3>
        
        {trend && (
          <p className="mt-2 text-xs flex items-center gap-1 font-medium">
            <span className={trendPositive ? "text-success" : "text-danger"}>
              {trend}
            </span>
            <span className="text-text-secondary font-normal ml-1">vs last month</span>
          </p>
        )}
      </div>
    </motion.div>
  );
}
