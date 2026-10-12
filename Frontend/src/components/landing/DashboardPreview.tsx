'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { ShieldCheck, Airplay, CheckCircle2, Wrench, AlertTriangle, ArrowRight, Database, Server, Info } from 'lucide-react';
import { getCampusStats } from '@/lib/campus-services';
import { motion } from 'framer-motion';
import { useAuth } from '@/contexts/AuthContext';

export default function DashboardPreview() {
  const { user } = useAuth();
  const [stats, setStats] = useState<{
    totalACs?: number;
    working?: number;
    faulty?: number;
    maintenance?: number;
    buildingsCount?: number;
  }>({});

  useEffect(() => {
    // Aggregate stats need an authenticated session; the landing page shows
    // zeros until then (no demo dataset since Phase 3).
    getCampusStats().then(setStats).catch(() => undefined);
  }, []);

  const portalHref = user ? (
    user.role === 'SUPER_ADMIN' ? '/admin/dashboard' :
    user.role === 'FACILITY_MANAGER' ? '/manager/dashboard' :
    user.role === 'TECHNICIAN' ? '/technician/dashboard' : '/viewer/dashboard'
  ) : '/login';

  return (
    <section className="py-24 bg-slate-950 overflow-hidden relative border-t border-slate-900">
      {/* Texture background accent */}
      <div className="absolute inset-0 bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 opacity-90"></div>
      
      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        
        {/* Header */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center max-w-3xl mx-auto mb-16"
        >
          <span className="text-xs font-bold text-sky-400 tracking-[0.2em] uppercase mb-3 block flex items-center justify-center gap-1.5">
            <Server className="w-4 h-4" /> Command Center Platform
          </span>
          <h3 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold text-white tracking-tight mb-4">
            Campus AC Command Portal
          </h3>
          <p className="text-base sm:text-lg text-slate-400 font-medium">
            Everything you need to manage NITTTR Chennai cooling infrastructure in one centralized dashboard.
          </p>
        </motion.div>

        {/* Dynamic Metric Cards Row */}
        <motion.div 
          initial={{ opacity: 0, y: 25 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-4xl mx-auto mb-12"
        >
          <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 backdrop-blur-md shadow-xl">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Total Assets</span>
              <Airplay className="w-4 h-4 text-blue-400" />
            </div>
            <p className="text-3xl font-black text-white">{stats.totalACs ?? '—'}</p>
            <p className="text-[11px] text-slate-500 mt-1 font-medium flex items-center gap-1">
              <Database className="w-3 h-3 text-blue-400" /> Mapped Database ACs
            </p>
          </div>

          <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 backdrop-blur-md shadow-xl">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Operational</span>
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            </div>
            <p className="text-3xl font-black text-emerald-400">{stats.working ?? '—'}</p>
            <p className="text-[11px] text-slate-500 mt-1 font-medium">Normal Working State</p>
          </div>

          <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 backdrop-blur-md shadow-xl">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">In Maintenance</span>
              <Wrench className="w-4 h-4 text-amber-400" />
            </div>
            <p className="text-3xl font-black text-amber-400">{stats.maintenance ?? '—'}</p>
            <p className="text-[11px] text-slate-500 mt-1 font-medium">Active Maintenance</p>
          </div>

          <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 backdrop-blur-md shadow-xl">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Faulty Units</span>
              <AlertTriangle className="w-4 h-4 text-rose-400" />
            </div>
            <p className="text-3xl font-black text-rose-400">{stats.faulty ?? '—'}</p>
            <p className="text-[11px] text-slate-500 mt-1 font-medium">Reported Faults</p>
          </div>
        </motion.div>

        {/* Dashboard Frame Mockup — illustrative, not live figures */}
        <div className="relative mx-auto w-full max-w-5xl">
           <div className="absolute -inset-1 bg-gradient-to-r from-blue-600 to-cyan-500 rounded-3xl blur-md opacity-30"></div>
           
           <div className="relative bg-slate-900 rounded-2xl shadow-2xl overflow-hidden border border-slate-800 aspect-video flex flex-col">
              {/* Portal Window Header */}
              <div className="h-12 bg-slate-950 border-b border-slate-800 flex justify-between items-center px-6">
                 <div className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center text-white">
                       <ShieldCheck className="w-4 h-4" />
                    </div>
                    <span className="font-bold text-white text-xs sm:text-sm tracking-tight">NITTTR AC Command Portal</span>
                 </div>
                 
                 <div className="flex items-center gap-3">
                    <span className="px-2.5 py-1 rounded-full bg-blue-950 text-blue-300 text-[11px] font-mono border border-blue-800/40">
                      Live Environment
                    </span>
                 </div>
              </div>
              
              {/* Dashboard Layout Mock */}
              <div className="flex-1 bg-slate-900 p-6 flex gap-6">
                 {/* Sidebar Mock */}
                 <div className="w-48 bg-slate-950/80 rounded-xl border border-slate-800 p-3 hidden md:flex flex-col gap-2.5">
                    {['Dashboard', 'Campus Mgmt', '3D Campus Map', 'AC Assets', 'Maintenance', 'Movements'].map((label, idx) => (
                       <div key={idx} className={`h-8 rounded-lg px-3 flex items-center text-xs font-semibold ${idx === 0 ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}>
                          {label}
                       </div>
                    ))}
                 </div>
                 
                 {/* Main Content Mock */}
                 <div className="flex-1 flex flex-col gap-4">
                    <div className="grid grid-cols-3 gap-4">
                       <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800">
                          <p className="text-[11px] text-slate-400 uppercase font-bold">Total Buildings</p>
                          <p className="text-xl font-black text-white mt-1">{stats.buildingsCount ?? '—'} Blocks</p>
                       </div>
                       <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800">
                          <p className="text-[11px] text-slate-400 uppercase font-bold">Active Jobs</p>
                          <p className="text-xl font-black text-amber-400 mt-1">5 Work Orders</p>
                       </div>
                       <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800">
                          <p className="text-[11px] text-slate-400 uppercase font-bold">Movement Requests</p>
                          <p className="text-xl font-black text-sky-400 mt-1">4 Active</p>
                       </div>
                    </div>
                    
                    {/* Simulated Table */}
                    <div className="flex-1 bg-slate-950/80 rounded-xl border border-slate-800 p-4 flex flex-col justify-between">
                       <div className="flex justify-between items-center border-b border-slate-800 pb-2">
                          <span className="text-xs font-bold text-slate-300">Recent AC Registry Log</span>
                          <span className="text-[10px] text-blue-400 font-mono">Live Sync</span>
                       </div>
                       <div className="space-y-2 py-2">
                          {[
                            { id: 'AC-127', loc: 'Academic Block A / AI Lab', status: 'Working', color: 'text-emerald-400' },
                            { id: 'AC-128', loc: 'Academic Block A / AI Lab', status: 'Working', color: 'text-emerald-400' },
                            { id: 'AC-129', loc: 'Academic Block A / Staff Room', status: 'Maintenance', color: 'text-amber-400' },
                          ].map((row, i) => (
                             <div key={i} className="flex justify-between items-center text-xs py-1 border-b border-slate-900">
                                <span className="font-mono font-bold text-slate-200">{row.id}</span>
                                <span className="text-slate-400">{row.loc}</span>
                                <span className={`font-bold ${row.color}`}>{row.status}</span>
                             </div>
                          ))}
                       </div>
                    </div>
                 </div>
              </div>
           </div>
        </div>

        {/* The frame above is a stylised product shot: its counts, job rows and
            registry entries are sample content, so they are labelled rather
            than presented as live database figures. The four metric cards
            above ARE live (getCampusStats → /dashboard/summary) and fall back
            to "—" when the visitor is not signed in. */}
        <p className="mt-5 flex items-center justify-center gap-1.5 text-center text-xs font-medium text-slate-500">
          <Info className="w-3.5 h-3.5 text-slate-500" aria-hidden="true" />
          Illustrative preview — the mockup&apos;s counts and rows are sample content, not live figures.
        </p>

        {/* Bottom CTA */}
        <div className="mt-16 text-center">
          <Link 
            href={portalHref}
            className="inline-flex items-center justify-center px-10 py-4 text-base font-bold rounded-full text-slate-950 bg-white hover:bg-slate-100 transition-all shadow-[0_0_25px_rgba(255,255,255,0.25)] hover:shadow-[0_0_35px_rgba(255,255,255,0.4)] active:scale-95 gap-2"
          >
            Access Management Portal
            <ArrowRight className="w-5 h-5" />
          </Link>
        </div>
      </div>
    </section>
  );
}
