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
import { loginUrl } from '@/lib/redirect';

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
      // Preserve the destination (same allow-list the login page enforces)
      // so post-login lands back here instead of the role home.
      router.replace(loginUrl(pathname));
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
    // Shell skeleton while auth initializes: static chrome shapes only, no
    // data fetching and no protected content — the gate above still decides
    // what renders once loading finishes.
    return (
      <div className="min-h-screen bg-slate-50 flex" aria-label="Loading" role="status">
        <div className="hidden sm:block w-60 shrink-0 bg-white border-r border-slate-200 p-4 space-y-3">
          <div className="h-8 rounded-lg bg-slate-100 animate-pulse" />
          <div className="h-8 rounded-lg bg-slate-100 animate-pulse" />
          <div className="h-8 rounded-lg bg-slate-100 animate-pulse" />
          <div className="h-8 rounded-lg bg-slate-100 animate-pulse" />
        </div>
        <div className="flex-1 flex flex-col min-w-0">
          <div className="h-14 bg-white border-b border-slate-200 flex items-center px-4">
            <div className="h-6 w-48 rounded-lg bg-slate-100 animate-pulse" />
          </div>
          <div className="p-4 sm:p-6 space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="h-20 rounded-2xl bg-white border border-slate-200 animate-pulse" />
              <div className="h-20 rounded-2xl bg-white border border-slate-200 animate-pulse" />
              <div className="h-20 rounded-2xl bg-white border border-slate-200 animate-pulse hidden lg:block" />
              <div className="h-20 rounded-2xl bg-white border border-slate-200 animate-pulse hidden lg:block" />
            </div>
            <div className="h-64 rounded-2xl bg-white border border-slate-200 animate-pulse" />
          </div>
        </div>
      </div>
    );
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
