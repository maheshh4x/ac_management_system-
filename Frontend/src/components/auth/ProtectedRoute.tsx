'use client';

/**
 * Client-side route UX guard (Phase 2, task 5 — "route protection that isn't
 * cosmetic"). The network boundary is enforced in src/proxy.ts (optimistic
 * cookie check) and authoritatively in Flask; this component only renders the
 * right screen: loading / login redirect / pending wall / 403.
 */
import React, { useEffect } from 'react';
import { useAuth, roleHome } from '@/contexts/AuthContext';
import { useRouter, usePathname } from 'next/navigation';
import { Permission, Role, hasPermission, canAccessRoute } from '@/lib/permissions';

const PUBLIC_PATHS = ['/login', '/signup'];

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (isLoading) return;

    const isPublicPath =
      PUBLIC_PATHS.some((p) => pathname === p) || pathname.startsWith('/a/');
    if (!user && !isPublicPath) {
      router.replace('/login');
      return;
    }
    if (!user) return;

    // Unapproved accounts see only the pending wall.
    const unapproved = user.status === 'pending' || user.status === 'rejected';
    if (unapproved && pathname !== '/pending') {
      router.replace('/pending');
      return;
    }
    if (!unapproved && pathname === '/pending') {
      router.replace(roleHome(user.role));
      return;
    }

    if (pathname === '/login') {
      router.replace(roleHome(user.role));
      return;
    }
    if (!canAccessRoute(user.role, pathname)) {
      router.replace('/403');
    }
  }, [user, isLoading, pathname, router]);

  if (isLoading) {
    return <div className="min-h-screen bg-slate-50 flex items-center justify-center">Loading...</div>;
  }

  if (!user && !PUBLIC_PATHS.includes(pathname) && !pathname.startsWith('/a/')) {
    return null;
  }

  return <>{children}</>;
}

export function ProtectedAction({ permission, children }: { permission: Permission; children: React.ReactNode }) {
  const { user } = useAuth();
  return hasPermission(user?.role || null, permission) ? <>{children}</> : null;
}

export function RoleGuard({ allowedRoles, children }: { allowedRoles: Role[]; children: React.ReactNode }) {
  const { user } = useAuth();
  return user && allowedRoles.includes(user.role) ? <>{children}</> : null;
}
