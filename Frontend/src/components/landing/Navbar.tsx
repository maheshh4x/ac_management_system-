'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { LogIn, Menu, X } from 'lucide-react';
import { collegeConfig } from '@/lib/collegeConfig';
import { motion, AnimatePresence } from 'framer-motion';

export default function Navbar() {
  const [activeSection, setActiveSection] = useState('home');
  const [isScrolled, setIsScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const navLinks = [
    { name: 'Home', href: '#home', id: 'home' },
    { name: 'About', href: '#about', id: 'about' },
    { name: 'Features', href: '#features', id: 'features' },
    { name: 'Campus', href: '#campus', id: 'campus' },
    { name: 'How It Works', href: '#how-it-works', id: 'how-it-works' },
    { name: 'Contact', href: '#contact', id: 'contact' },
  ];

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 40);

      const sectionIds = navLinks.map(link => link.id);
      const scrollPosition = window.scrollY + 120;

      for (let i = sectionIds.length - 1; i >= 0; i--) {
        const section = document.getElementById(sectionIds[i]);
        if (section && section.offsetTop <= scrollPosition) {
          setActiveSection(sectionIds[i]);
          break;
        }
      }
      
      if (window.scrollY < 80) {
        setActiveSection('home');
      }
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleSmoothScroll = (e: React.MouseEvent<HTMLAnchorElement, MouseEvent>, href: string) => {
    if (href.startsWith('#')) {
      e.preventDefault();
      const id = href.replace('#', '');
      const element = document.getElementById(id);
      if (element) {
        const yOffset = -80;
        const y = element.getBoundingClientRect().top + window.pageYOffset + yOffset;
        window.scrollTo({ top: y, behavior: 'smooth' });
        setActiveSection(id);
        setMobileMenuOpen(false);
      } else if (id === 'home') {
        window.scrollTo({ top: 0, behavior: 'smooth' });
        setActiveSection('home');
        setMobileMenuOpen(false);
      }
    }
  };

  return (
    <>
      <header 
        className={`fixed top-0 left-0 right-0 w-full z-50 transition-all duration-300 ${
          isScrolled ? 'bg-white/90 backdrop-blur-md shadow-md py-3' : 'bg-white py-4 border-b border-slate-100'
        }`}
      >
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center">
            
            {/* LEFT: Logo & Institution Name */}
            <Link href="/" className="flex items-center gap-3.5 group">
               <div className="relative w-11 h-11 rounded-full overflow-hidden border border-slate-200 shadow-sm group-hover:scale-105 transition-transform flex items-center justify-center bg-blue-900 text-white font-bold text-xs">
                 <img 
                   src={collegeConfig.images.logo} 
                   alt="NITTTR Chennai Logo" 
                   className="w-full h-full object-cover"
                   onError={(e) => { 
                     // Fallback if image load fails
                     e.currentTarget.style.display = 'none'; 
                   }} 
                 />
                 <span className="select-none">NC</span>
               </div>

               <div className="flex flex-col">
                  <span className="font-extrabold text-slate-900 text-lg tracking-tight leading-none group-hover:text-blue-600 transition-colors">
                    {collegeConfig.shortName}
                  </span>
                  <span className="text-[11px] text-slate-500 font-medium tracking-normal hidden sm:block mt-0.5 max-w-xs truncate">
                    {collegeConfig.name}
                  </span>
               </div>
            </Link>

            {/* RIGHT: Desktop Links */}
            <nav className="hidden lg:flex items-center space-x-1">
              {navLinks.map((link) => {
                const isActive = activeSection === link.id;
                return (
                  <a 
                    key={link.name} 
                    href={link.href}
                    onClick={(e) => handleSmoothScroll(e, link.href)}
                    className="relative px-4 py-2 text-[14px] font-semibold transition-colors group"
                  >
                    <span className={`relative z-10 transition-colors ${isActive ? 'text-blue-600 font-bold' : 'text-slate-600 group-hover:text-slate-900'}`}>
                      {link.name}
                    </span>
                    {isActive && (
                      <motion.div 
                        layoutId="activeNavIndicator"
                        className="absolute inset-0 bg-blue-50/80 rounded-full z-0"
                        transition={{ type: "spring", stiffness: 350, damping: 30 }}
                      />
                    )}
                  </a>
                );
              })}
            </nav>

            {/* Portal Login Button */}
            <div className="hidden lg:flex items-center">
              <Link 
                href="/login" 
                className="flex items-center px-6 py-2.5 rounded-full text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 transition-all shadow-md hover:shadow-lg active:scale-95 gap-2"
              >
                Portal Login
                <LogIn className="w-4 h-4" />
              </Link>
            </div>

            {/* Mobile Toggle */}
            <div className="lg:hidden flex items-center">
               <button 
                 onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                 className="text-slate-700 p-2 rounded-xl hover:bg-slate-100 transition-colors focus:outline-none"
                 aria-label="Toggle Navigation Menu"
               >
                  {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
               </button>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile Menu Panel */}
      <AnimatePresence>
        {mobileMenuOpen && (
          <motion.div 
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-white pt-24 pb-8 px-6 lg:hidden flex flex-col h-screen overflow-y-auto"
          >
            <div className="flex flex-col space-y-3">
              {navLinks.map((link) => (
                <a 
                  key={link.name} 
                  href={link.href}
                  onClick={(e) => handleSmoothScroll(e, link.href)}
                  className={`text-xl font-bold py-3 border-b border-slate-100 flex items-center justify-between ${
                    activeSection === link.id ? 'text-blue-600' : 'text-slate-800'
                  }`}
                >
                  {link.name}
                  <span className="text-slate-300 text-sm">→</span>
                </a>
              ))}
            </div>
            
            <div className="mt-8 pt-6">
               <Link 
                href="/login" 
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center justify-center w-full px-6 py-4 rounded-xl text-base font-bold text-white bg-blue-600 active:scale-95 transition-transform gap-2 shadow-lg"
              >
                Portal Login
                <LogIn className="w-5 h-5" />
              </Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
