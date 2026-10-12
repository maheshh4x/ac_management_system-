'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { ArrowRight, Play, MapPin, MessageSquare, X, CheckCircle2, ShieldCheck, Airplay, Wrench, Layers } from 'lucide-react';
import { collegeConfig } from '@/lib/collegeConfig';
import { motion, AnimatePresence, Variants } from 'framer-motion';

export default function HeroSection() {
  const [modalOpen, setModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'video' | 'features'>('video');

  // Close modal on escape key
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setModalOpen(false);
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, []);

  const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: { 
      opacity: 1,
      transition: { staggerChildren: 0.15, delayChildren: 0.2 }
    }
  };

  const itemVariants: Variants = {
    hidden: { opacity: 0, y: 25 },
    visible: { 
      opacity: 1, 
      y: 0, 
      transition: { type: "spring", stiffness: 100, damping: 15 }
    }
  };

  return (
    <section id="home" className="relative w-full min-h-[680px] lg:h-[720px] pt-24 pb-16 lg:py-0 flex items-center overflow-hidden bg-[#0a1128]">
      
      {/* Background Image & Overlay Container */}
      <motion.div 
        initial={{ opacity: 0, scale: 1.05 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 1.4, ease: "easeOut" }}
        className="absolute inset-0 z-0"
      >
        <img 
          src={collegeConfig.images.hero} 
          alt={`Main campus building of ${collegeConfig.name}`} 
          className="w-full h-full object-cover object-[center_25%]"
        />
        
        {/* Dark Navy Gradient Overlay on Left */}
        <div className="absolute inset-0 bg-gradient-to-r from-[#0a1128] via-[#0a1128]/95 to-transparent w-full lg:w-[65%]" />
        {/* Bottom subtle gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-[#0a1128] via-transparent to-transparent opacity-80" />
      </motion.div>

      {/* Hero Content */}
      <div className="relative z-10 max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 w-full">
        
        <motion.div 
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          className="max-w-2xl text-left"
        >
          {/* Badge */}
          <motion.div variants={itemVariants}>
            <span className="inline-flex items-center gap-2 py-1.5 px-4 rounded-full bg-blue-950/80 border border-blue-400/30 text-blue-400 text-xs font-bold tracking-wide mb-6 backdrop-blur-md shadow-sm">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              Smart Campus Initiative
            </span>
          </motion.div>

          {/* Heading */}
          <motion.h1 variants={itemVariants} className="text-4xl sm:text-5xl lg:text-[3.8rem] font-extrabold text-white tracking-tight leading-[1.08] mb-6">
            Intelligent Air-Conditioning <br />
            <span className="bg-gradient-to-r from-blue-400 via-sky-300 to-cyan-300 bg-clip-text text-transparent">
              Asset Management
            </span>
          </motion.h1>
          
          {/* Description */}
          <motion.p variants={itemVariants} className="max-w-xl text-base sm:text-lg text-slate-200 mb-10 font-medium leading-relaxed">
            A centralized platform to monitor, track, maintain and manage air-conditioning assets across the {collegeConfig.shortName} campus.
          </motion.p>

          {/* Buttons */}
          <motion.div variants={itemVariants} className="flex flex-col sm:flex-row gap-4">
            <Link 
              href="/login" 
              className="group flex items-center justify-center px-8 py-3.5 text-[15px] font-bold rounded-full text-white bg-blue-600 hover:bg-blue-500 shadow-[0_0_25px_rgba(37,99,235,0.4)] hover:shadow-[0_0_35px_rgba(37,99,235,0.6)] active:scale-95 transition-all duration-300"
            >
              Access Management Portal
              <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
            </Link>
            
            <button 
              onClick={() => setModalOpen(true)}
              className="group flex items-center justify-center px-7 py-3.5 text-[15px] font-bold rounded-full text-white bg-slate-900/80 hover:bg-slate-800 border border-slate-700/60 backdrop-blur-md transition-all active:scale-95 duration-300"
            >
              <div className="w-6 h-6 rounded-full bg-white text-blue-900 flex items-center justify-center mr-2.5 group-hover:scale-110 transition-transform shadow-sm">
                 <Play className="w-3 h-3 ml-0.5 fill-current" />
              </div>
              Watch Overview
            </button>
          </motion.div>
        </motion.div>
      </div>
      
      {/* Location Indicator Badge */}
      <motion.div 
        initial={{ opacity: 0, x: 40 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 1.1, duration: 0.8, ease: "easeOut" }}
        className="absolute right-8 bottom-12 hidden md:flex items-center gap-3.5 bg-slate-950/70 backdrop-blur-md px-5 py-3 rounded-2xl border border-white/10 z-10 shadow-xl"
      >
         <div className="p-2 bg-blue-600/20 rounded-xl border border-blue-400/30">
           <MapPin className="text-blue-400 w-5 h-5" />
         </div>
         <div>
            <p className="text-white font-bold text-sm leading-tight">Main Campus</p>
            <p className="text-slate-400 text-xs mt-0.5">{collegeConfig.shortName}</p>
         </div>
      </motion.div>

      {/* Watch Overview Modal */}
      <AnimatePresence>
        {modalOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/80 backdrop-blur-md p-4"
            onClick={() => setModalOpen(false)}
          >
            <motion.div 
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ type: "spring", stiffness: 300, damping: 28 }}
              className="relative w-full max-w-4xl bg-white rounded-3xl shadow-2xl overflow-hidden border border-slate-200"
              onClick={e => e.stopPropagation()}
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between px-6 py-4 bg-slate-900 text-white border-b border-slate-800">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-blue-600 rounded-lg">
                    <ShieldCheck className="w-5 h-5 text-white" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold">NITTTR AC Command Center Overview</h2>
                    <p className="text-xs text-slate-400">System Workflow & Interactive Capability Walkthrough</p>
                  </div>
                </div>

                <button 
                  onClick={() => setModalOpen(false)}
                  className="p-2 bg-white/10 hover:bg-white/20 rounded-full transition-colors text-slate-300 hover:text-white"
                  aria-label="Close modal"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Modal Tabs */}
              <div className="flex border-b border-slate-200 bg-slate-50 px-6 pt-3 gap-2">
                <button
                  onClick={() => setActiveTab('video')}
                  className={`px-4 py-2.5 text-xs font-bold rounded-t-xl transition-all border-t border-x ${
                    activeTab === 'video'
                      ? 'bg-white text-blue-600 border-slate-200 border-b-white -mb-px'
                      : 'text-slate-500 hover:text-slate-800 border-transparent'
                  }`}
                >
                  Interactive Walkthrough
                </button>
                <button
                  onClick={() => setActiveTab('features')}
                  className={`px-4 py-2.5 text-xs font-bold rounded-t-xl transition-all border-t border-x ${
                    activeTab === 'features'
                      ? 'bg-white text-blue-600 border-slate-200 border-b-white -mb-px'
                      : 'text-slate-500 hover:text-slate-800 border-transparent'
                  }`}
                >
                  Key System Highlights
                </button>
              </div>
              
              {/* Modal Content */}
              <div className="p-6 bg-white min-h-[320px]">
                {activeTab === 'video' ? (
                  <div className="flex flex-col items-center justify-center p-8 bg-slate-950 rounded-2xl text-center text-white border border-slate-800">
                    <div className="w-16 h-16 rounded-full bg-blue-600/20 border border-blue-400/30 flex items-center justify-center mb-4 text-blue-400 shadow-lg">
                      <Play className="w-8 h-8 ml-1 fill-current" />
                    </div>
                    <h3 className="text-xl font-bold mb-2">Smart Campus AC Management Platform</h3>
                    <p className="max-w-md text-sm text-slate-300 mb-6 leading-relaxed">
                      Watch how NITTTR Chennai standardizes AC asset tracking, QR scanning, maintenance work orders, and 3D digital twin spatial view.
                    </p>
                    <div className="grid grid-cols-3 gap-4 w-full max-w-lg mt-2 text-left">
                      <div className="p-3 bg-slate-900 rounded-xl border border-slate-800">
                        <Airplay className="w-5 h-5 text-blue-400 mb-1" />
                        <p className="text-xs font-bold">Asset Registry</p>
                      </div>
                      <div className="p-3 bg-slate-900 rounded-xl border border-slate-800">
                        <Wrench className="w-5 h-5 text-amber-400 mb-1" />
                        <p className="text-xs font-bold">Maintenance Jobs</p>
                      </div>
                      <div className="p-3 bg-slate-900 rounded-xl border border-slate-800">
                        <Layers className="w-5 h-5 text-emerald-400 mb-1" />
                        <p className="text-xs font-bold">3D Campus Map</p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {[
                      { title: 'Role-Based Authentication', desc: 'Separate interfaces for Super Admin, Facility Manager, Technician, and Viewer.' },
                      { title: '3D Spatial Twin', desc: 'Interactive campus model representing buildings, floors, rooms, and AC units.' },
                      { title: 'QR Asset Identification', desc: 'Instant QR code scanning on physical AC units for mobile maintenance logging.' },
                      { title: 'Movement Approval Workflow', desc: 'Strict multi-stage approval for asset relocation across campus blocks.' }
                    ].map((h, i) => (
                      <div key={i} className="p-4 rounded-xl bg-slate-50 border border-slate-200">
                        <div className="flex items-center gap-2 mb-1.5">
                          <CheckCircle2 className="w-4 h-4 text-blue-600" />
                          <h3 className="font-bold text-slate-900 text-sm">{h.title}</h3>
                        </div>
                        <p className="text-xs text-slate-600 font-medium leading-relaxed">{h.desc}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="flex justify-between items-center px-6 py-4 bg-slate-50 border-t border-slate-200">
                <span className="text-xs text-slate-500 font-medium">NITTTR Chennai Facility Management</span>
                <Link 
                  href="/login" 
                  onClick={() => setModalOpen(false)}
                  className="px-5 py-2 text-xs font-bold rounded-full text-white bg-blue-600 hover:bg-blue-700 transition-colors shadow-sm"
                >
                  Enter Portal →
                </Link>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
