export type Role = 'SUPER_ADMIN' | 'FACILITY_MANAGER' | 'TECHNICIAN' | 'VIEWER';

export type Permission = 
  | 'VIEW_AC' 
  | 'CREATE_AC' 
  | 'EDIT_AC' 
  | 'DEACTIVATE_AC' 
  | 'MANAGE_CAMPUS' 
  | 'MANAGE_MAINTENANCE' 
  | 'CREATE_MOVEMENT_REQUEST' 
  | 'APPROVE_MOVEMENT' 
  | 'COMPLETE_MOVEMENT'
  | 'VIEW_REPORTS' 
  | 'MANAGE_USERS' 
  | 'VIEW_AUDIT_LOGS' 
  | 'MANAGE_SETTINGS';

const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  SUPER_ADMIN: [
    'VIEW_AC', 'CREATE_AC', 'EDIT_AC', 'DEACTIVATE_AC', 
    'MANAGE_CAMPUS', 'MANAGE_MAINTENANCE', 'CREATE_MOVEMENT_REQUEST', 
    'APPROVE_MOVEMENT', 'COMPLETE_MOVEMENT', 'VIEW_REPORTS', 'MANAGE_USERS', 
    'VIEW_AUDIT_LOGS', 'MANAGE_SETTINGS'
  ],
  FACILITY_MANAGER: [
    'VIEW_AC', 'CREATE_AC', 'MANAGE_CAMPUS', 'MANAGE_MAINTENANCE',
    'APPROVE_MOVEMENT', 'COMPLETE_MOVEMENT', 'VIEW_REPORTS'
  ],
  TECHNICIAN: [
    'VIEW_AC', 'CREATE_MOVEMENT_REQUEST', 'COMPLETE_MOVEMENT', 'MANAGE_MAINTENANCE'
  ],
  VIEWER: [
    'VIEW_AC'
  ]
};

export const hasPermission = (role: Role | null, permission: Permission): boolean => {
  if (!role) return false;
  return ROLE_PERMISSIONS[role].includes(permission);
};

/**
 * Role snapshot (Phase 2): the server-derived role from /api/v1/me, set by
 * AuthContext after session bootstrap. Never read from localStorage — client
 * state is a UX convenience only; Flask enforces all permissions.
 */
let roleSnapshot: Role | null = null;

/** Called by AuthContext when the session bootstraps/changes/clears. */
export const setAuthSnapshot = (role: Role | null): void => {
  roleSnapshot = role;
};

export const getCurrentUserRole = (): Role | null => roleSnapshot;

export const canEditInventory = (role: Role | null): boolean => role === 'SUPER_ADMIN';
export const canManageMaintenance = (role: Role | null): boolean =>
  role === 'SUPER_ADMIN' || role === 'FACILITY_MANAGER' || role === 'TECHNICIAN';
export const canUploadMaintenanceUpdates = (role: Role | null): boolean =>
  role === 'SUPER_ADMIN' || role === 'FACILITY_MANAGER' || role === 'TECHNICIAN';
export const canApproveMaintenanceRequests = (role: Role | null): boolean =>
  role === 'SUPER_ADMIN' || role === 'FACILITY_MANAGER';
export const canApproveRequests = (role: Role | null): boolean =>
  role === 'SUPER_ADMIN' || role === 'FACILITY_MANAGER';
export const canEditProfile = (role: Role | null): boolean => role !== 'VIEWER';

/**
 * Role → the asset detail page inside that role's portal (Phase 6): the
 * public QR landing (/a/<ref>) redirects signed-in visitors here so a
 * sticker scan lands on the page they are allowed to see.
 */
export const roleAssetDetail = (role: Role | null | undefined, ref: string): string => {
  const portal =
    role === 'SUPER_ADMIN'
      ? 'admin'
      : role === 'FACILITY_MANAGER'
        ? 'manager'
        : role === 'TECHNICIAN'
          ? 'technician'
          : 'viewer';
  return `/${portal}/assets/${encodeURIComponent(ref)}`;
};

/**
 * Route → roles table (Phase 2, task 5). Explicit and complete: any route not
 * listed is DENIED for authenticated portal users (previously unknown paths
 * were silently allowed, so a VIEWER could reach almost everything).
 * Auth enforcement itself happens server-side; this table only drives UX
 * redirects in ProtectedRoute.
 */
const ROUTE_ROLES: ReadonlyArray<{ prefix: string; roles: readonly Role[] }> = [
  { prefix: "/admin", roles: ["SUPER_ADMIN"] },
  { prefix: "/manager", roles: ["SUPER_ADMIN", "FACILITY_MANAGER"] },
  { prefix: "/technician", roles: ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN"] },
  { prefix: "/viewer", roles: ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"] },
  { prefix: "/campus", roles: ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"] },
  { prefix: "/403", roles: ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"] },
  { prefix: "/pending", roles: ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"] },
];

// Truly public: reachable without any session. /pending needs a session
// (it's rendered from the user's own profile) so it lives in ROUTE_ROLES.
const PUBLIC_PATHS = ["/login", "/signup"];

export const canAccessRoute = (role: Role | null, path: string): boolean => {
  if (PUBLIC_PATHS.some((p) => path === p || path.startsWith(p + "/"))) return true;
  if (path === "/" || path.startsWith("/a/")) return true; // landing + public QR page

  if (!role) return false;
  const match = ROUTE_ROLES.find((r) => path === r.prefix || path.startsWith(r.prefix + "/"));
  if (!match) return false; // unknown portal path: deny, never default-allow
  return match.roles.includes(role);
};
