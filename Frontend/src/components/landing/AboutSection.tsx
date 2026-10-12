'use client';

import React from 'react';
import { Building, Award, Compass, ShieldCheck, CheckCircle2 } from 'lucide-react';
import { collegeConfig } from '@/lib/collegeConfig';
import { motion } from 'framer-motion';

export default function AboutSection() {
  return (
    <section id="about" className="py-24 bg-slate-50 relative overflow-hidden">
      {/* Background accents */}
      <div className="absolute -top-24 -right-24 w-96 h-96 bg-blue-100/50 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-24 -left-24 w-96 h-96 bg-cyan-100/50 rounded-full blur-3xl pointer-events-none" />

      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
          
          {/* Left Column - Visual Card */}
          <motion.div 
            initial={{ opacity: 0, x: -30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7 }}
            className="lg:col-span-5"
          >
            <div className="relative rounded-3xl overflow-hidden shadow-2xl border border-slate-200 bg-white p-2">
              <div className="relative h-[380px] rounded-2xl overflow-hidden">
                <img 
                  src={collegeConfig.images.hero} 
                  alt="NITTTR Chennai Campus" 
                  className="w-full h-full object-cover"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-slate-950/90 via-slate-950/40 to-transparent" />
                
                <div className="absolute bottom-6 left-6 right-6 text-white">
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-blue-600/90 text-xs font-bold uppercase tracking-wider mb-2">
                    <ShieldCheck className="w-3.5 h-3.5" /> Est. {collegeConfig.established}
                  </span>
                  <h3 className="text-xl font-bold">{collegeConfig.shortName}</h3>
                  <p className="text-xs text-slate-300 mt-1 font-medium">{collegeConfig.location}</p>
                </div>
              </div>

              {/* Stats strip */}
              <div className="grid grid-cols-3 gap-2 p-4 bg-white text-center">
                <div className="p-3 bg-slate-50 rounded-xl">
                  <p className="text-2xl font-black text-blue-600">60+</p>
                  <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mt-0.5">Years Legacy</p>
                </div>
                <div className="p-3 bg-slate-50 rounded-xl">
                  <p className="text-2xl font-black text-slate-900">MoE</p>
                  <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mt-0.5">Govt. of India</p>
                </div>
                <div className="p-3 bg-slate-50 rounded-xl">
                  <p className="text-2xl font-black text-cyan-600">14</p>
                  <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mt-0.5">Campus Blocks</p>
                </div>
              </div>
            </div>
          </motion.div>

          {/* Right Column - Factual Info */}
          <motion.div 
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="lg:col-span-7"
          >
            <span className="text-xs font-bold text-blue-600 tracking-[0.2em] uppercase mb-3 block">
              Institutional Context
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900 tracking-tight leading-tight mb-6">
              About NITTTR Chennai & <br />
              <span className="text-blue-600">Smart Facility Management</span>
            </h2>

            <p className="text-base sm:text-lg text-slate-600 leading-relaxed mb-6 font-normal">
              {collegeConfig.name} ({collegeConfig.shortName}) is an autonomous institution under the Ministry of Education, Government of India. Established in {collegeConfig.established}, NITTTR Chennai plays a pivotal role in upgrading technical teacher training, educational pedagogy, and institutional capacity across Southern India.
            </p>

            <p className="text-sm sm:text-base text-slate-600 leading-relaxed mb-8">
              The Smart Campus Air-Conditioning Command Center is an institutional digital initiative designed to centralize climate control asset tracking, preventive maintenance lifecycle management, spatial 3D mapping, and equipment audit logs across all campus facilities in Taramani.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {[
                { title: 'Ministry of Education Initiative', desc: 'Autonomous premier institution standardizing campus infrastructure.' },
                { title: 'Centralized Asset Tracking', desc: 'Digital QR identity and spatial mapping for every AC unit.' },
                { title: 'Predictive & Preventive Service', desc: 'Minimizing equipment downtime across classrooms & labs.' },
                { title: 'Transparent Maintenance Logs', desc: 'Full audit history of relocation, repair, and warranty records.' }
              ].map((item, idx) => (
                <div key={idx} className="flex items-start gap-3 p-4 rounded-2xl bg-white border border-slate-200/80 shadow-sm">
                  <CheckCircle2 className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="font-bold text-slate-900 text-sm">{item.title}</h3>
                    <p className="text-xs text-slate-500 mt-1 leading-normal font-medium">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>

          </motion.div>

        </div>

      </div>
    </section>
  );
}
