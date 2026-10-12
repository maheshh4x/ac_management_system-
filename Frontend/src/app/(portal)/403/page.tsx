'use client';

import React from 'react';
import { ShieldAlert, ArrowLeft } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useRouter } from 'next/navigation';

export default function ForbiddenPage() {
  const { user } = useAuth();
  const router = useRouter();

  return (
    <div className="min-h-[80vh] flex flex-col items-center justify-center p-6 text-center">
      <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mb-6">
        <ShieldAlert className="w-8 h-8 text-red-600" />
      </div>
      <h1 className="text-4xl font-bold text-slate-900 mb-2">Access Denied</h1>
      <p className="text-lg text-slate-600 mb-8 max-w-md">
        You do not have permission to access this page based on your current role ({user?.role?.replace('_', ' ') || 'Unknown'}).
      </p>
      <button 
        onClick={() => router.back()}
        className="flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium shadow-sm"
      >
        <ArrowLeft className="w-4 h-4" /> Go Back
      </button>
    </div>
  );
}
