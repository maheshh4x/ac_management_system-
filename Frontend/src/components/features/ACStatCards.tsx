// src/components/features/ACStatCards.tsx
'use client';

import React, { useEffect, useState } from 'react';
import { motion, useInView } from 'framer-motion';
import { Airplay, Zap, CheckCircle2, AlertOctagon, Building2, MapPin, Database } from 'lucide-react';
import { ACStatistics } from '@/lib/types/asset';

interface ACStatCardsProps {
  stats?: ACStatistics;
  loading?: boolean;
  error?: string | null;
  onStatClick?: (statusFilter: string) => void;
}

const AnimatedCounter = ({ value, append = '' }: { value: number; append?: string }) => {
  const [count, setCount] = useState(0);
  const ref = React.useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-50px" });

  useEffect(() => {
    if (!isInView) return;
    let start = 0;
    const duration = 1000;
    const startTime = performance.now();
    
    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      // easeOutQuart
      const easeProgress = 1 - Math.pow(1 - progress, 4);
      setCount(Math.floor(easeProgress * value));
      
      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };
    requestAnimationFrame(animate);
  }, [value, isInView]);

  return <span ref={ref}>{count}{append}</span>;
};

export const ACStatCards: React.FC<ACStatCardsProps> = ({
  stats,
  loading = false,
  error = null,
  onStatClick
}) => {
  if (loading || !stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
        {[1, 2, 3, 4, 5, 6, 7].map(i => (
          <div key={i} className="glass-card p-5 animate-pulse">
            <div className="h-4 w-20 bg-slate-200 rounded mb-3" />
            <div className="flex justify-between items-end">
              <div className="h-8 w-16 bg-slate-300 rounded mb-1" />
              <div className="h-4 w-4 bg-slate-200 rounded-full" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm flex items-center justify-between">
        <span>Failed to load statistics: {error}</span>
      </div>
    );
  }

  const cards = [
    {
      id: 'All',
      title: 'Total AC Units',
      value: stats.total,
      icon: Airplay,
      accent: 'from-blue-500/20 to-cyan-500/20 text-blue-600',
      iconAccent: 'text-blue-500',
      borderAccent: 'group-hover:border-blue-400/50'
    },
    {
      id: 'Capacity',
      title: 'Installed Capacity',
      value: stats.installedCapacity,
      append: ' Ton',
      icon: Zap,
      accent: 'from-purple-500/20 to-indigo-500/20 text-purple-600',
      iconAccent: 'text-purple-500',
      borderAccent: 'group-hover:border-purple-400/50'
    },
    {
      id: 'Working',
      title: 'Operational Units',
      value: stats.working,
      icon: CheckCircle2,
      accent: 'from-emerald-500/20 to-green-500/20 text-emerald-600',
      iconAccent: 'text-emerald-500',
      borderAccent: 'group-hover:border-emerald-400/50'
    },
    {
      id: 'Maintenance',
      title: 'Action Needs',
      value: stats.underMaintenance + stats.faulty,
      icon: AlertOctagon,
      accent: 'from-rose-500/20 to-red-500/20 text-rose-600',
      iconAccent: 'text-rose-500',
      borderAccent: 'group-hover:border-rose-400/50'
    },
    {
      id: 'Depts',
      title: 'Departments',
      value: stats.departmentsCount,
      icon: Building2,
      accent: 'from-amber-500/20 to-yellow-500/20 text-amber-600',
      iconAccent: 'text-amber-500',
      borderAccent: 'group-hover:border-amber-400/50'
    },
    {
      id: 'Locations',
      title: 'Locations / Rooms',
      value: stats.locationsCount,
      icon: MapPin,
      accent: 'from-fuchsia-500/20 to-pink-500/20 text-fuchsia-600',
      iconAccent: 'text-fuchsia-500',
      borderAccent: 'group-hover:border-fuchsia-400/50'
    },
    {
      id: 'Completeness',
      title: 'Data Completeness',
      value: stats.dataCompletenessPct,
      append: '%',
      icon: Database,
      accent: 'from-teal-500/20 to-cyan-500/20 text-teal-600',
      iconAccent: 'text-teal-500',
      borderAccent: 'group-hover:border-teal-400/50'
    }
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
      {cards.map((card, idx) => {
        const Icon = card.icon;
        return (
          <motion.div
            key={card.title}
            initial={{ opacity: 0, y: 20, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.4, delay: idx * 0.08, ease: "easeOut" }}
            onClick={() => onStatClick && onStatClick(card.id)}
            className={`glass-card p-4 flex flex-col justify-between cursor-pointer group relative overflow-hidden ${card.borderAccent}`}
          >
            {/* Subtle Gradient Backdrop Blob */}
            <div className={`absolute -right-4 -bottom-4 w-20 h-20 rounded-full bg-gradient-to-br ${card.accent} blur-2xl group-hover:scale-150 transition-transform duration-700 pointer-events-none`} />

            <div className="z-10 flex flex-col gap-1">
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 group-hover:text-slate-800 transition-colors">
                {card.title}
              </span>
            </div>
            
            <div className="z-10 flex items-end justify-between mt-3">
              <div className="text-2xl font-extrabold text-slate-900 group-hover:scale-105 origin-left transition-transform duration-300">
                <AnimatedCounter value={card.value} append={card.append} />
              </div>
              <Icon size={18} className={`mb-1 opacity-70 group-hover:opacity-100 transition-opacity ${card.iconAccent}`} />
            </div>
          </motion.div>
        );
      })}
    </div>
  );
};
