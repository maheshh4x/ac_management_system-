'use client';

import React from 'react';
import Link from 'next/link';
import { Map, ChevronRight, Layers, Eye, CheckCircle2 } from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '@/contexts/AuthContext';

export default function CampusVisualization() {
  const { user } = useAuth();
  const campusHref = user ? '/campus' : '/login';

  return (
    <section id="campus" className="py-20 bg-white overflow-hidden relative">
      {/* Decorative background blob */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-full pointer-events-none opacity-30">
        <div className="absolute top-[-5%] right-[-5%] w-96 h-96 bg-blue-100 rounded-full mix-blend-multiply filter blur-3xl opacity-70"></div>
        <div className="absolute top-[20%] left-[-5%] w-72 h-72 bg-cyan-100 rounded-full mix-blend-multiply filter blur-3xl opacity-70"></div>
      </div>

      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
          
          {/* Left Text Box */}
          <motion.div 
            initial={{ opacity: 0, x: -30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
            className="lg:col-span-6"
          >
            <span className="text-xs font-bold text-blue-600 tracking-[0.2em] uppercase mb-3 block flex items-center gap-1.5">
              <Map className="w-4 h-4" /> 3D Digital Twin Spatial Model
            </span>
            <h3 className="text-3xl sm:text-4xl font-extrabold text-slate-900 tracking-tight mb-6 leading-tight">
              Visualize NITTTR Chennai <br />
              <span className="text-blue-600">Building Hierarchy & Assets</span>
            </h3>
            <p className="text-base sm:text-lg text-slate-600 mb-8 leading-relaxed font-medium">
              Explore buildings, floors, rooms and AC assets through a centralized spatial model. Interactive navigation provides instant spatial context for maintenance tickets and asset relocation across campus blocks.
            </p>
            
            <div className="space-y-3.5 mb-10">
               {[
                 'Hierarchical structure (Campus > Building > Floor > Room)',
                 'Real-time status indicators (Working, Fault, Under Maintenance)',
                 'Direct search & filtering by AC serial number or block name'
               ].map((item, i) => (
                 <div key={i} className="flex items-start gap-3">
                    <div className="flex-shrink-0 h-5 w-5 rounded-full bg-blue-100 flex items-center justify-center border border-blue-200 mt-0.5">
                       <CheckCircle2 className="w-3.5 h-3.5 text-blue-600" />
                    </div>
                    <p className="text-sm text-slate-700 font-medium">{item}</p>
                 </div>
               ))}
            </div>

            <Link 
              href={campusHref}
              className="inline-flex items-center px-7 py-3.5 rounded-full bg-slate-900 text-white font-bold hover:bg-blue-600 transition-all shadow-lg hover:shadow-xl group gap-2"
            >
              Explore 3D Campus
              <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Link>
          </motion.div>

          {/* Right Visual 3D Preview Card */}
          <motion.div 
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="lg:col-span-6 relative"
          >
            <div className="relative bg-slate-950 rounded-3xl shadow-2xl overflow-hidden border border-slate-800 flex flex-col">
               {/* Faux Window Header */}
               <div className="h-11 bg-slate-900 border-b border-slate-800 flex justify-between items-center px-4">
                  <div className="flex items-center space-x-2">
                     <div className="w-3 h-3 rounded-full bg-rose-500/80"></div>
                     <div className="w-3 h-3 rounded-full bg-amber-500/80"></div>
                     <div className="w-3 h-3 rounded-full bg-emerald-500/80"></div>
                  </div>
                  <div className="text-xs font-semibold text-slate-400 font-mono flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-blue-400" /> NITTTR_3D_Campus_Viewer.twin
                  </div>
                  <span className="text-[10px] bg-blue-900/60 text-blue-300 px-2 py-0.5 rounded font-mono border border-blue-700/50">
                    Live Canvas
                  </span>
               </div>
               
               {/* Simulated 3D Environment Canvas */}
               <div className="relative h-[360px] bg-gradient-to-b from-slate-900 via-slate-950 to-black overflow-hidden flex items-center justify-center p-6">
                  {/* Grid overlay */}
                  <div className="absolute inset-0 opacity-20" style={{ backgroundImage: 'linear-gradient(rgba(56, 189, 248, 0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(56, 189, 248, 0.2) 1px, transparent 1px)', backgroundSize: '30px 30px' }}></div>
                  
                  {/* Building blocks isometric mock */}
                  <div className="relative z-10 w-full max-w-md space-y-4">
                     <div className="grid grid-cols-2 gap-4">
                        <div className="bg-slate-800/80 border border-slate-700 rounded-2xl p-4 shadow-xl backdrop-blur-md hover:border-blue-500 transition-colors">
                           <div className="flex justify-between items-center mb-2">
                              <span className="text-xs font-bold text-white font-mono">Academic Block A</span>
                              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                           </div>
                           <p className="text-[11px] text-slate-400 mb-3">CSE & AI Labs • Floor 0-3</p>
                           <div className="flex gap-1.5">
                              <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px] font-bold">18 ACs OK</span>
                              <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 text-[10px] font-bold">1 Maint</span>
                           </div>
                        </div>

                        <div className="bg-slate-800/80 border border-slate-700 rounded-2xl p-4 shadow-xl backdrop-blur-md hover:border-blue-500 transition-colors">
                           <div className="flex justify-between items-center mb-2">
                              <span className="text-xs font-bold text-white font-mono">Admin Block</span>
                              <span className="w-2 h-2 rounded-full bg-blue-400"></span>
                           </div>
                           <p className="text-[11px] text-slate-400 mb-3">Director Office • Ground</p>
                           <div className="flex gap-1.5">
                              <span className="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 text-[10px] font-bold">12 ACs OK</span>
                           </div>
                        </div>
                     </div>

                     {/* Highlighted Selected Block Card */}
                     <div className="bg-gradient-to-r from-blue-900/60 to-slate-900/80 border border-blue-500/60 rounded-2xl p-4 shadow-[0_0_30px_rgba(37,99,235,0.25)] backdrop-blur-md">
                        <div className="flex justify-between items-center mb-3">
                           <div className="flex items-center gap-2">
                              <div className="w-2.5 h-2.5 bg-emerald-400 rounded-full"></div>
                              <span className="text-white font-bold text-sm">Training & Labs Block C</span>
                           </div>
                           <span className="text-blue-300 font-mono text-xs font-bold">3D Active</span>
                        </div>
                        <div className="grid grid-cols-6 gap-2 pt-1">
                            {[...Array(6)].map((_, i) => (
                               <div key={i} className={`h-5 rounded-md flex items-center justify-center text-[10px] font-mono font-bold ${
                                 i === 2 ? 'bg-amber-500/80 text-white' : i === 4 ? 'bg-rose-500/80 text-white' : 'bg-emerald-500/40 text-emerald-200'
                               }`}>
                                 AC-{127 + i}
                               </div>
                            ))}
                        </div>
                     </div>
                  </div>

                  {/* Overlay button on 3D box */}
                  <div className="absolute bottom-4 right-4 z-20">
                    <Link
                      href={campusHref}
                      className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-xl shadow-lg flex items-center gap-1.5 backdrop-blur-md transition-all"
                    >
                      <Eye className="w-3.5 h-3.5" /> Open Full 3D Map
                    </Link>
                  </div>
               </div>
            </div>
          </motion.div>
          
        </div>
      </div>
    </section>
  );
}
