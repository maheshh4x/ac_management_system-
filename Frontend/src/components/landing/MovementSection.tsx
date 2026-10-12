'use client';

import React from 'react';
import { ArrowLeftRight, Check, MapPin } from 'lucide-react';

export default function MovementSection() {
  return (
    <section className="py-24 bg-slate-50 border-t border-slate-100">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="lg:grid lg:grid-cols-2 lg:gap-16 items-center">
          
          <div className="mb-12 lg:mb-0">
             <h2 className="text-base font-bold text-blue-600 tracking-wide uppercase mb-2">Asset Relocation</h2>
             <h3 className="text-3xl font-extrabold text-slate-900 tracking-tight sm:text-4xl mb-6">
                Track Every AC Movement
             </h3>
             <p className="text-lg text-slate-600 mb-6 leading-relaxed">
                Relocating an AC unit between departments? Our built-in movement tracking enforces an approval workflow to ensure assets never go missing.
             </p>
             
             <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <h4 className="font-bold text-slate-900 mb-4 text-sm uppercase tracking-wide flex items-center">
                    Workflow Statuses
                </h4>
                <div className="grid grid-cols-2 gap-4">
                   {['Pending Approval', 'Approved', 'Movement In Progress', 'Completed & Verified'].map((status, i) => (
                      <div key={i} className="flex items-center text-sm font-medium text-slate-600">
                          <Check className={`w-4 h-4 mr-2 ${i === 3 ? 'text-green-500' : 'text-blue-500'}`} /> {status}
                      </div>
                   ))}
                </div>
             </div>
          </div>

          {/* Visualization */}
          <div className="relative">
             <div className="bg-white rounded-3xl p-6 shadow-xl border border-slate-100 relative z-10 overflow-hidden">
                <div className="absolute top-0 right-0 w-32 h-32 bg-blue-50 rounded-bl-full -z-10"></div>
                
                <div className="flex justify-between items-center mb-8 pb-6 border-b border-slate-100">
                   <div>
                      <p className="text-xs text-slate-500 font-bold uppercase tracking-wider mb-1">Asset</p>
                      <p className="text-xl font-black text-slate-900 font-mono">AC-127</p>
                   </div>
                   <div className="px-3 py-1 bg-green-50 text-green-700 text-xs font-bold rounded-full border border-green-100">
                      Movement Completed
                   </div>
                </div>

                <div className="relative">
                   {/* Dotted path */}
                   <div className="absolute left-6 top-8 bottom-8 w-px border-l-2 border-dashed border-slate-300"></div>
                   
                   {/* Source */}
                   <div className="flex items-start mb-8 relative">
                      <div className="w-12 h-12 rounded-full bg-slate-50 border border-slate-200 flex items-center justify-center z-10">
                         <MapPin className="w-5 h-5 text-slate-400" />
                      </div>
                      <div className="ml-6 pt-1">
                         <p className="text-xs text-slate-500 font-bold uppercase tracking-wider mb-1">Origin (Previous)</p>
                         <p className="font-bold text-slate-900">AI Lab</p>
                         <p className="text-sm text-slate-500">Academic Block A, Floor 2</p>
                      </div>
                   </div>

                   {/* Middle indicator */}
                   <div className="flex items-center absolute left-[21px] top-1/2 -translate-y-1/2 -ml-2.5 z-20 bg-white">
                      <div className="p-1 rounded-full bg-blue-100 border border-blue-200">
                         <ArrowLeftRight className="w-4 h-4 text-blue-600" />
                      </div>
                   </div>

                   {/* Destination */}
                   <div className="flex items-start relative mt-12">
                      <div className="w-12 h-12 rounded-full bg-blue-50 border border-blue-200 flex items-center justify-center z-10 shadow-[0_0_15px_rgba(59,130,246,0.3)]">
                         <MapPin className="w-6 h-6 text-blue-600" />
                      </div>
                      <div className="ml-6 pt-1">
                         <p className="text-xs text-blue-600 font-bold uppercase tracking-wider mb-1">Destination (Current)</p>
                         <p className="font-bold text-slate-900 text-lg">Director Office</p>
                         <p className="text-sm text-slate-500">Admin Block, Ground Floor</p>
                      </div>
                   </div>
                </div>
             </div>
          </div>
          
        </div>
      </div>
    </section>
  );
}
