'use client';

import React from 'react';
import { QrCode, Search, MapPin, Wrench, ShieldCheck, ArrowRight, Activity, Smartphone } from 'lucide-react';
import { motion } from 'framer-motion';

const mainSteps = [
  {
    num: '01',
    title: 'Locate',
    description: 'Find AC assets spatially across campus blocks, floors, and rooms.',
    icon: MapPin,
    color: 'text-blue-400',
    border: 'border-blue-500/40'
  },
  {
    num: '02',
    title: 'Monitor',
    description: 'View real-time asset status, warranty details, and health parameters.',
    icon: Activity,
    color: 'text-emerald-400',
    border: 'border-emerald-500/40'
  },
  {
    num: '03',
    title: 'Maintain',
    description: 'Create, assign, and track preventive and corrective maintenance jobs.',
    icon: Wrench,
    color: 'text-amber-400',
    border: 'border-amber-500/40'
  },
  {
    num: '04',
    title: 'Manage',
    description: 'Manage asset movements, approvals, audit logs, and lifecycle.',
    icon: ShieldCheck,
    color: 'text-purple-400',
    border: 'border-purple-500/40'
  }
];

const qrFlowSteps = [
  'SCAN QR',
  'IDENTIFY AC',
  'VIEW LOCATION',
  'VIEW STATUS',
  'VIEW MAINTENANCE',
  'LOCATE IN 3D'
];

export default function QRWorkflowSection() {
  return (
    <section id="how-it-works" className="py-24 bg-slate-950 text-white overflow-hidden relative border-t border-slate-900">
      {/* Background radial accent */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[500px] bg-blue-950/30 rounded-full blur-3xl pointer-events-none" />

      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        
        {/* Section Header */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center max-w-3xl mx-auto mb-20"
        >
          <span className="text-xs font-bold text-blue-400 tracking-[0.2em] uppercase mb-3 block">
            System Operational Workflow
          </span>
          <h3 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight mb-5">
            How It Works
          </h3>
          <p className="text-base sm:text-lg text-slate-400 font-medium leading-relaxed">
            End-to-end management framework connecting physical infrastructure with real-time campus intelligence.
          </p>
        </motion.div>

        {/* 4-Step Main Workflow */}
        <div className="relative mb-24">
           {/* Animated Connecting Path Line */}
           <div className="hidden lg:block absolute top-1/2 left-12 right-12 h-0.5 bg-gradient-to-r from-blue-600 via-emerald-500 to-purple-600 -translate-y-10 z-0 opacity-40"></div>
           
           <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8 relative z-10">
              {mainSteps.map((step, index) => (
                <motion.div 
                  key={index} 
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, delay: index * 0.12 }}
                  className="flex flex-col items-center text-center group"
                >
                   <div className={`w-20 h-20 rounded-3xl bg-slate-900 border ${step.border} flex items-center justify-center mb-6 shadow-2xl relative group-hover:scale-105 group-hover:border-blue-400 transition-all duration-300`}>
                      <step.icon className={`w-8 h-8 ${step.color} transition-transform group-hover:scale-110`} />
                      
                      {/* Step Number Tag */}
                      <div className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-blue-600 text-white font-black text-xs flex items-center justify-center border-2 border-slate-950 shadow-md">
                         {step.num}
                      </div>
                   </div>
                   
                   <h4 className="text-xl font-extrabold text-white mb-2">{step.title}</h4>
                   <p className="text-slate-400 text-sm leading-relaxed max-w-xs">{step.description}</p>
                </motion.div>
              ))}
           </div>
        </div>

        {/* Digital Identity & QR Section */}
        <motion.div 
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="bg-slate-900/90 border border-slate-800 rounded-3xl p-8 lg:p-12 shadow-2xl relative overflow-hidden"
        >
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            
            <div className="lg:col-span-5">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-blue-950 text-blue-400 text-xs font-bold uppercase tracking-wider mb-4 border border-blue-800/40">
                <Smartphone className="w-3.5 h-3.5" /> Mobile QR Integration
              </span>
              <h4 className="text-2xl sm:text-3xl font-extrabold text-white mb-4">
                Every AC Has a Digital Identity
              </h4>
              <p className="text-slate-400 text-sm sm:text-base leading-relaxed mb-6 font-medium">
                Physical AC units across NITTTR Chennai are tagged with unique QR code labels. Technicians scan the barcode using mobile devices to load live asset profiles, maintenance history, and diagnostic logs.
              </p>
              <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 text-xs text-slate-400 font-mono">
                <span className="text-blue-400 font-bold">Planned & Portal Workflow:</span> Technicians access camera scanner inside Technician app view.
              </div>
            </div>

            {/* QR Flow Banner */}
            <div className="lg:col-span-7">
              <div className="bg-slate-950 rounded-2xl p-6 border border-slate-800">
                <h5 className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-6 flex items-center gap-2">
                  <QrCode className="w-4 h-4 text-blue-400" /> Digital Identity Lifecycle Flow
                </h5>

                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {qrFlowSteps.map((flowStep, idx) => (
                    <div 
                      key={idx} 
                      className="p-3 bg-slate-900 rounded-xl border border-slate-800 flex items-center justify-between text-xs font-bold text-slate-200"
                    >
                      <span className="truncate">{flowStep}</span>
                      {idx < qrFlowSteps.length - 1 ? (
                        <ArrowRight className="w-3.5 h-3.5 text-blue-400 flex-shrink-0 ml-1" />
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>

          </div>
        </motion.div>

      </div>
    </section>
  );
}
