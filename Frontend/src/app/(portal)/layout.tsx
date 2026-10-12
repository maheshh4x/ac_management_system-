import React from "react";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { CampusProvider } from "@/contexts/CampusContext";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <CampusProvider>
        <DashboardLayout>
          {children}
        </DashboardLayout>
      </CampusProvider>
    </ProtectedRoute>
  );
}
