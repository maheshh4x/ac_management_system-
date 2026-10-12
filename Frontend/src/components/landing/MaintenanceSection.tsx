'use client';

import React from 'react';
import { AlertCircle, FileClock, UserCheck, CheckCircle2 } from 'lucide-react';

export default function MaintenanceSection() {
  return (
    <section className="py-24 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="lg:grid lg:grid-cols-2 lg:gap-16 items-center flex flex-col-reverse lg:flex-row">
          
          {/* Visual Workflow mockup */}
          <div className="w-full relative mt-12 lg:mt-0">
             <div className="absolute inset-0 bg-blue-50 rounded-3xl transform -rotate-3 scale-105"></div>
             <div className="relative bg-white rounded-3xl p-8 shadow-xl border border-slate-100">
                <h4 className="font-bold text-slate-900 mb-6 border-b pb-4">Maintenance Lifecycle</h4>
                
                <div className="space-y-6">
                   <div className="flex items-start">
                      <div className="flex-shrink-0 mt-1">
                         <AlertCircle className="w-5 h-5 text-red-500" />
                      </div>
                      <div className="ml-4">
                         <p className="text-sm font-bold text-slate-900">Issue Reported</p>
                         <p className="text-xs text-slate-500 mt-1">NNot Cooling - AC-127 - AI Lab</p>
                      </div>
                   </div>
                   
                   <div className="ml-2.5 w-0.5 h-6 bg-slate-200"></div>

                   <div className="flex items-start">
                      <div className="flex-shrink-0 mt-1">
                         <FileClock className="w-5 h-5 text-amber-500" />
                      </div>
                      <div className="ml-4">
                         <p className="text-sm font-bold text-slate-900">Job Created & Assigned</p>
                         <p className="text-xs text-slate-500 mt-1">Assigned to Tech Team Alpha</p>
                      </div>
                   </div>

                   <div className="ml-2.5 w-0.5 h-6 bg-slate-200"></div>
                   
                   <div className="flex items-start">
                      <div className="flex-shrink-0 mt-1">
                         <UserCheck className="w-5 h-5 text-blue-500" />
                      </div>
                      <div className="ml-4">
                         <p className="text-sm font-bold text-slate-900">Work In Progress</p>
                         <p className="text-xs text-slate-500 mt-1">Technician checking compressor</p>
                      </div>
                   </div>

                   <div className="ml-2.5 w-0.5 h-6 bg-slate-200"></div>

                   <div className="flex items-start">
                      <div className="flex-shrink-0 mt-1 shadow-[0_0_15px_rgba(34,197,94,0.4)] rounded-full bg-white">
                         <CheckCircle2 className="w-5 h-5 text-green-500" />
                      </div>
                      <div className="ml-4">
                         <p className="text-sm font-bold text-slate-900">Repair Completed</p>
                         <p className="text-xs text-slate-500 mt-1">Gas refilled. Status: Working</p>
                      </div>
                   </div>
                </div>
             </div>
          </div>

          <div>
             <h2 className="text-base font-bold text-blue-600 tracking-wide uppercase mb-2">Smart Maintenance</h2>
             <h3 className="text-3xl font-extrabold text-slate-900 tracking-tight sm:text-4xl mb-6">
                From Issue to Resolution
             </h3>
             <p className="text-lg text-slate-600 mb-6 leading-relaxed">
                Streamline your campus maintenance operations. The system tracks every fault report from the exact moment it's logged until the required repairs are fully completed and verified.
             </p>
             <p className="text-lg text-slate-600 leading-relaxed block border-l-4 border-blue-500 pl-4 italic">
                "Reduce downtime, increase accountability, and keep historical repair logs for audit and performance monitoring."
             </p>
          </div>
          
        </div>
      </div>
    </section>
  );
}
