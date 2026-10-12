'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { Leaf, Wind, CheckCircle } from 'lucide-react';

export function SmartCampusCard() {
  return (
    <div className="relative overflow-hidden h-full min-h-[300px] w-full bg-gradient-to-br from-[#073B35] to-[#0B4F45] text-white p-8 flex flex-col justify-end">
      {/* Decorative background blobs */}
      <div className="absolute top-0 right-0 w-64 h-64 bg-emerald-500/20 rounded-full blur-3xl -translate-y-1/2 translate-x-1/3" />
      <div className="absolute bottom-0 left-0 w-48 h-48 bg-teal-500/20 rounded-full blur-2xl translate-y-1/3 -translate-x-1/4" />

      <div className="relative z-10 flex flex-col items-start gap-4 h-full pt-4">
        <div className="bg-white/10 backdrop-blur-md px-3 py-1 rounded-full text-xs font-semibold text-emerald-200 border border-white/10 uppercase tracking-widest">
          Sustainability
        </div>
        
        <div className="mt-auto">
          <h2 className="text-3xl font-extrabold tracking-tight mb-2">Efficient Air.<br/>Greener Campus.</h2>
          <p className="text-emerald-100/80 text-sm max-w-[250px] mb-6">
            NITTTR Chennai is optimizing AC usage to reduce carbon footprint through smart tracking.
          </p>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center gap-2">
              <Leaf size={16} className="text-emerald-400" />
              <div className="text-xs font-medium text-emerald-50">Smart Tracked</div>
            </div>
            <div className="flex items-center gap-2">
              <Wind size={16} className="text-cyan-400" />
              <div className="text-xs font-medium text-emerald-50">Eco-friendly</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
