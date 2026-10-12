import React from 'react';
import Navbar from '@/components/landing/Navbar';
import HeroSection from '@/components/landing/HeroSection';
import FeatureStrip from '@/components/landing/FeatureStrip';
import AboutSection from '@/components/landing/AboutSection';
import SystemOverview from '@/components/landing/SystemOverview';
import CampusVisualization from '@/components/landing/CampusVisualization';
import QRWorkflowSection from '@/components/landing/QRWorkflowSection';
import MaintenanceSection from '@/components/landing/MaintenanceSection';
import MovementSection from '@/components/landing/MovementSection';
import DashboardPreview from '@/components/landing/DashboardPreview';
import Footer from '@/components/landing/Footer';

export const metadata = {
  title: 'NITTTR Chennai - Smart Campus AC Asset & Facility Command Center',
  description: 'Intelligent Air-Conditioning Asset, Maintenance & Spatial Digital Twin Management Portal for NITTTR Chennai.',
};

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-white selection:bg-blue-200 overflow-x-hidden">
      <Navbar />
      <HeroSection />
      <FeatureStrip />
      <AboutSection />
      <SystemOverview />
      <CampusVisualization />
      <QRWorkflowSection />
      <MaintenanceSection />
      <MovementSection />
      <DashboardPreview />
      <Footer />
    </main>
  );
}
