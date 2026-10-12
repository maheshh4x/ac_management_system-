'use client';

import React from 'react';
import Link from 'next/link';
import { ShieldCheck, Mail, Phone, MapPin, ExternalLink, Globe, ArrowUp } from 'lucide-react';
import { collegeConfig } from '@/lib/collegeConfig';
import { motion } from 'framer-motion';

export default function Footer() {
  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <footer id="contact" className="bg-slate-950 pt-20 pb-8 text-white border-t border-slate-900 relative">
      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-12 gap-10 pb-16 border-b border-slate-900">
          
          {/* Institutional Info */}
          <div className="lg:col-span-5">
            <div className="flex items-center gap-3 mb-5">
              <div className="relative w-10 h-10 rounded-full overflow-hidden border border-slate-700 bg-blue-900 flex items-center justify-center text-xs font-bold">
                 <img 
                   src={collegeConfig.images.logo} 
                   alt="NITTTR Chennai Logo" 
                   className="w-full h-full object-cover"
                   onError={(e) => { e.currentTarget.style.display = 'none'; }} 
                 />
                 <span>NC</span>
              </div>
              <div>
                <h4 className="font-extrabold text-white text-lg leading-tight">{collegeConfig.shortName}</h4>
                <p className="text-[11px] text-slate-400 font-medium">Smart Campus AC Command Platform</p>
              </div>
            </div>
            
            <p className="text-slate-400 text-sm leading-relaxed mb-6 max-w-md">
              {collegeConfig.name} ({collegeConfig.shortName}) is an autonomous institution under the Ministry of Education, Government of India, located in Taramani, Chennai.
            </p>

            <div className="flex items-center gap-3">
              <a 
                href={collegeConfig.officialWebsite} 
                target="_blank" 
                rel="noopener noreferrer" 
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 text-blue-400 hover:text-blue-300 text-xs font-bold border border-slate-800 transition-colors"
              >
                <Globe className="w-3.5 h-3.5" /> Official Website <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
          
          {/* Quick Navigation Links */}
          <div className="lg:col-span-3">
            <h5 className="text-white font-bold mb-4 uppercase tracking-wider text-xs">
              Quick Navigation
            </h5>
            <ul className="space-y-2.5 text-sm">
              <li>
                <a href="#home" className="text-slate-400 hover:text-white transition-colors">Home</a>
              </li>
              <li>
                <a href="#about" className="text-slate-400 hover:text-white transition-colors">About NITTTR</a>
              </li>
              <li>
                <a href="#features" className="text-slate-400 hover:text-white transition-colors">System Features</a>
              </li>
              <li>
                <a href="#campus" className="text-slate-400 hover:text-white transition-colors">3D Digital Twin Map</a>
              </li>
              <li>
                <a href="#how-it-works" className="text-slate-400 hover:text-white transition-colors">Operational Workflow</a>
              </li>
              <li>
                <Link href="/login" className="text-blue-400 hover:text-blue-300 font-bold transition-colors inline-flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4" /> Portal Sign In
                </Link>
              </li>
            </ul>
          </div>
          
          {/* Contact Details */}
          <div className="lg:col-span-4">
            <h5 className="text-white font-bold mb-4 uppercase tracking-wider text-xs">
              Verified Contact Information
            </h5>
            <ul className="space-y-4 text-sm">
              <li className="flex items-start gap-3">
                <MapPin className="text-blue-400 w-5 h-5 flex-shrink-0 mt-0.5" />
                <span className="text-slate-300">{collegeConfig.contact.address}</span>
              </li>
              <li className="flex items-center gap-3">
                <Phone className="text-blue-400 w-5 h-5 flex-shrink-0" />
                <span className="text-slate-300">{collegeConfig.contact.phone}</span>
              </li>
              <li className="flex items-center gap-3">
                <Mail className="text-blue-400 w-5 h-5 flex-shrink-0" />
                <a href={`mailto:${collegeConfig.contact.email}`} className="text-slate-300 hover:text-blue-400 transition-colors">
                  {collegeConfig.contact.email}
                </a>
              </li>
            </ul>
          </div>

        </div>
        
        {/* Bottom copyright & back to top */}
        <div className="pt-8 flex flex-col sm:flex-row justify-between items-center gap-4 text-xs text-slate-400">
          <p>
            &copy; {new Date().getFullYear()} {collegeConfig.name}. Ministry of Education, Govt. of India.
          </p>

          <div className="flex items-center gap-4">
             <span>Smart Campus Platform v2.5</span>
             <button 
               onClick={scrollToTop}
               className="p-2 rounded-full bg-slate-900 hover:bg-slate-800 text-slate-300 hover:text-white transition-colors flex items-center gap-1 font-semibold"
               title="Back to top"
             >
                Top <ArrowUp className="w-3.5 h-3.5" />
             </button>
          </div>
        </div>

      </div>
    </footer>
  );
}
