'use client';

import React from 'react';
import Link from 'next/link';
import { Monitor, Building2, Layers, Wrench, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '@/contexts/AuthContext';

export default function FeatureStrip() {
  const { user } = useAuth();

  // Smart target resolution based on user role or fallback to login
  const getTarget = (module: 'assets' | 'campus' | 'maintenance') => {
    if (!user) return '/login';
    const roleBase = user.role === 'SUPER_ADMIN' ? '/admin' : 
                     user.role === 'FACILITY_MANAGER' ? '/manager' : 
                     user.role === 'TECHNICIAN' ? '/technician' : '/viewer';
    
    if (module === 'campus') return '/campus';
    if (module === 'assets') return `${roleBase}/assets`;
    if (module === 'maintenance') return `${roleBase}/maintenance?tab=maintenance`;
    return `${roleBase}/dashboard`;
  };

  const features = [
    {
      icon: Monitor,
      title: 'AC Assets',
      desc: 'Manage AC asset information',
      color: 'text-blue-600',
      bg: 'bg-blue-50 border-blue-100',
      module: 'assets' as const
    },
    {
      icon: Building2,
      title: 'Buildings',
      desc: 'Manage campus blocks',
      color: 'text-emerald-600',
      bg: 'bg-emerald-50 border-emerald-100',
      module: 'campus' as const
    },
    {
      icon: Layers,
      title: 'Floors & Rooms',
      desc: 'Organized campus hierarchy',
      color: 'text-purple-600',
      bg: 'bg-purple-50 border-purple-100',
      module: 'campus' as const
    },
    {
      icon: Wrench,
      title: 'Maintenance',
      desc: 'Track & resolve issues',
      color: 'text-amber-600',
      bg: 'bg-amber-50 border-amber-100',
      module: 'maintenance' as const
    }
  ];

  const containerVariants = {
    hidden: { opacity: 0, y: 30 },
    visible: {
      opacity: 1,
      y: 0,
      transition: {
        duration: 0.6,
        staggerChildren: 0.1,
        delayChildren: 0.4
      }
    }
  };

  const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 120, damping: 14 } }
  };

  return (
    <section id="features" className="relative z-20 max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 -mt-14 sm:-mt-16">
      <motion.div 
        variants={containerVariants}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "0px 0px -50px 0px" }}
        className="bg-white rounded-3xl shadow-2xl border border-slate-100 p-4 sm:p-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        {features.map((feature, idx) => (
          <Link 
            key={idx}
            href={getTarget(feature.module)}
            className="group block cursor-pointer focus:outline-none"
          >
            <motion.div 
              variants={itemVariants}
              className="flex items-center justify-between p-4 rounded-2xl bg-slate-50/60 hover:bg-slate-50 border border-slate-100/80 transition-all duration-300 group-hover:shadow-md group-hover:-translate-y-1"
            >
              <div className="flex items-center gap-4">
                <div className={`w-13 h-13 rounded-2xl ${feature.bg} border flex items-center justify-center group-hover:scale-110 transition-transform duration-300 shadow-sm`}>
                  <feature.icon className={`w-6 h-6 ${feature.color} group-hover:rotate-6 transition-transform`} />
                </div>
                <div>
                  <h2 className="font-extrabold text-slate-900 text-base group-hover:text-blue-600 transition-colors">{feature.title}</h2>
                  <p className="text-slate-500 text-xs mt-0.5 font-medium">{feature.desc}</p>
                </div>
              </div>
              
              <div className="w-8 h-8 rounded-full border border-slate-200 flex items-center justify-center text-slate-400 group-hover:bg-blue-600 group-hover:border-blue-600 group-hover:text-white transition-all duration-300 shadow-sm flex-shrink-0">
                 <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
              </div>
            </motion.div>
          </Link>
        ))}
      </motion.div>
    </section>
  );
}
