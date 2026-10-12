'use client';

import React from 'react';
import Link from 'next/link';
import { Map, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '@/contexts/AuthContext';

export default function SystemOverview() {
  const { user } = useAuth();
  const campusHref = user ? '/campus' : '/login';

  return (
    <section className="pt-20 pb-12 bg-white relative overflow-hidden">
      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">
        
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="flex flex-col lg:flex-row lg:items-center justify-between gap-8 pb-12 border-b border-slate-100"
        >
           
           {/* Left text block */}
           <div className="max-w-2xl">
              <span className="text-xs font-bold text-blue-600 tracking-[0.2em] uppercase mb-3 block">
                Campus Infrastructure
              </span>
              <h3 className="text-3xl sm:text-4xl lg:text-[2.6rem] font-extrabold text-slate-900 tracking-tight leading-[1.15]">
                Smart Campus <br />
                <span className="text-blue-600">Infrastructure & Digital Twin</span>
              </h3>
           </div>
           
           {/* Right text block & button */}
           <div className="max-w-xl lg:max-w-md lg:border-l-2 lg:border-slate-100 lg:pl-8">
              <p className="text-base text-slate-600 font-medium leading-relaxed mb-6">
                Explore the campus, its buildings, floors and rooms in an interactive 3D environment. Locate AC assets, view their status, and manage maintenance efficiently.
              </p>

              <Link 
                href={campusHref}
                className="inline-flex items-center justify-center px-7 py-3.5 text-sm font-bold rounded-full text-white bg-blue-600 hover:bg-blue-700 transition-all shadow-md hover:shadow-lg active:scale-95 group gap-2"
              >
                <Map className="w-4 h-4" />
                Explore 3D Campus
                <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </Link>
           </div>
        </motion.div>

      </div>
    </section>
  );
}
