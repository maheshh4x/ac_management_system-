/**
 * Phase 8.2 — permission table unit tests (task 8.2).
 *
 * `permissions.ts` is the FE half of the RBAC contract: it decides which
 * portal a role lands on, which navigation/actions render, and which routes
 * `ProtectedRoute` allows. Flask is still the authority (nothing here is
 * enforced), but a silently-wrong table here is exactly the class of bug the
 * Phase 2 "deny by default" fix existed to stop — so the matrix is asserted
 * exhaustively rather than by spot-check.
 */
import { afterEach, describe, expect, it } from "vitest";

import {
  canAccessRoute,
  canApproveMaintenanceRequests,
  canApproveRequests,
  canEditInventory,
  canEditProfile,
  canManageMaintenance,
  canUploadMaintenanceUpdates,
  getCurrentUserRole,
  hasPermission,
  roleAssetDetail,
  setAuthSnapshot,
  type Permission,
  type Role,
} from "./permissions";

const ROLES: Role[] = ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"];

const ALL_PERMISSIONS: Permission[] = [
  "VIEW_AC",
  "CREATE_AC",
  "EDIT_AC",
  "DEACTIVATE_AC",
  "MANAGE_CAMPUS",
  "MANAGE_MAINTENANCE",
  "CREATE_MOVEMENT_REQUEST",
  "APPROVE_MOVEMENT",
  "COMPLETE_MOVEMENT",
  "VIEW_REPORTS",
  "MANAGE_USERS",
  "VIEW_AUDIT_LOGS",
  "MANAGE_SETTINGS",
];

/** The table, written out independently of the implementation. */
const EXPECTED: Record<Role, Permission[]> = {
  SUPER_ADMIN: [...ALL_PERMISSIONS],
  FACILITY_MANAGER: [
    "VIEW_AC",
    "CREATE_AC",
    "MANAGE_CAMPUS",
    "MANAGE_MAINTENANCE",
    "APPROVE_MOVEMENT",
    "COMPLETE_MOVEMENT",
    "VIEW_REPORTS",
  ],
  TECHNICIAN: ["VIEW_AC", "CREATE_MOVEMENT_REQUEST", "COMPLETE_MOVEMENT", "MANAGE_MAINTENANCE"],
  VIEWER: ["VIEW_AC"],
};

describe("hasPermission", () => {
  it("matches the expected matrix for every role × permission pair", () => {
    for (const role of ROLES) {
      const expected = EXPECTED[role];
      for (const permission of ALL_PERMISSIONS) {
        expect(
          hasPermission(role, permission),
          `${role} × ${permission} should be ${expected.includes(permission)}`,
        ).toBe(expected.includes(permission));
      }
    }
  });

  it("denies everything when no role is bound yet", () => {
    for (const permission of ALL_PERMISSIONS) {
      expect(hasPermission(null, permission)).toBe(false);
    }
  });

  it("keeps the migration-era guardrails: VIEWER may only look", () => {
    expect(hasPermission("VIEWER", "VIEW_AC")).toBe(true);
    expect(hasPermission("VIEWER", "CREATE_MOVEMENT_REQUEST")).toBe(false);
    expect(hasPermission("VIEWER", "VIEW_REPORTS")).toBe(false);
    expect(hasPermission("VIEWER", "MANAGE_USERS")).toBe(false);
  });

  it("never lets a manager approve maintenance work they cannot see", () => {
    expect(canApproveMaintenanceRequests("FACILITY_MANAGER")).toBe(true);
    expect(canManageMaintenance("FACILITY_MANAGER")).toBe(true);
    expect(canManageMaintenance("VIEWER")).toBe(false);
    expect(hasPermission("TECHNICIAN", "APPROVE_MOVEMENT")).toBe(false);
    expect(hasPermission("TECHNICIAN", "MANAGE_USERS")).toBe(false);
  });

  it("grants COMPLETE_MOVEMENT to the three roles that can finish a move", () => {
    expect(hasPermission("SUPER_ADMIN", "COMPLETE_MOVEMENT")).toBe(true);
    expect(hasPermission("FACILITY_MANAGER", "COMPLETE_MOVEMENT")).toBe(true);
    expect(hasPermission("TECHNICIAN", "COMPLETE_MOVEMENT")).toBe(true);
    expect(hasPermission("VIEWER", "COMPLETE_MOVEMENT")).toBe(false);
  });
});

describe("auth snapshot", () => {
  afterEach(() => setAuthSnapshot(null));

  it("round-trips the server-derived role and clears back to null", () => {
    expect(getCurrentUserRole()).toBeNull();
    setAuthSnapshot("TECHNICIAN");
    expect(getCurrentUserRole()).toBe("TECHNICIAN");
    setAuthSnapshot(null);
    expect(getCurrentUserRole()).toBeNull();
  });

  it("drives the derived predicates once bound", () => {
    setAuthSnapshot("SUPER_ADMIN");
    expect(canEditInventory(getCurrentUserRole())).toBe(true);
    setAuthSnapshot("VIEWER");
    expect(canEditInventory(getCurrentUserRole())).toBe(false);
    expect(canEditProfile(getCurrentUserRole())).toBe(false);
  });
});

describe("role helpers", () => {
  it("restricts inventory edits to SUPER_ADMIN", () => {
    expect(canEditInventory("SUPER_ADMIN")).toBe(true);
    expect(canEditInventory("FACILITY_MANAGER")).toBe(false);
    expect(canEditInventory("TECHNICIAN")).toBe(false);
    expect(canEditInventory("VIEWER")).toBe(false);
    expect(canEditInventory(null)).toBe(false);
  });

  it("lets managers and technicians upload maintenance updates", () => {
    expect(canUploadMaintenanceUpdates("SUPER_ADMIN")).toBe(true);
    expect(canUploadMaintenanceUpdates("FACILITY_MANAGER")).toBe(true);
    expect(canUploadMaintenanceUpdates("TECHNICIAN")).toBe(true);
    expect(canUploadMaintenanceUpdates("VIEWER")).toBe(false);
    expect(canApproveRequests("TECHNICIAN")).toBe(false);
    expect(canApproveRequests("FACILITY_MANAGER")).toBe(true);
  });

  it("routes each role to its own asset detail portal", () => {
    expect(roleAssetDetail("SUPER_ADMIN", "AC-001")).toBe("/admin/assets/AC-001");
    expect(roleAssetDetail("FACILITY_MANAGER", "AC-001")).toBe("/manager/assets/AC-001");
    expect(roleAssetDetail("TECHNICIAN", "AC-001")).toBe("/technician/assets/AC-001");
    expect(roleAssetDetail("VIEWER", "AC-001")).toBe("/viewer/assets/AC-001");
    expect(roleAssetDetail(null, "AC-001")).toBe("/viewer/assets/AC-001");
    expect(roleAssetDetail(undefined, "AC-001")).toBe("/viewer/assets/AC-001");
  });

  it("URL-encodes the asset reference in the portal path", () => {
    expect(roleAssetDetail("SUPER_ADMIN", "A/B?C")).toBe("/admin/assets/A%2FB%3FC");
  });
});

describe("canAccessRoute", () => {
  it("allows the unauthenticated entry points without a role", () => {
    expect(canAccessRoute(null, "/login")).toBe(true);
    expect(canAccessRoute(null, "/signup")).toBe(true);
    expect(canAccessRoute(null, "/login?next=/admin")).toBe(false); // query is not a path prefix
    expect(canAccessRoute(null, "/")).toBe(true);
    expect(canAccessRoute(null, "/a/AC-001")).toBe(true); // public QR landing
  });

  it("denies every portal path when there is no session", () => {
    for (const path of ["/admin", "/admin/dashboard", "/manager", "/technician", "/viewer", "/campus", "/pending"]) {
      expect(canAccessRoute(null, path), path).toBe(false);
    }
  });

  it("scopes each portal to its allowed roles", () => {
    const cases: Array<[Role, string, boolean]> = [
      ["SUPER_ADMIN", "/admin", true],
      ["SUPER_ADMIN", "/admin/users", true],
      ["FACILITY_MANAGER", "/admin", false],
      ["FACILITY_MANAGER", "/manager", true],
      ["TECHNICIAN", "/manager", false],
      ["TECHNICIAN", "/technician/jobs/w1", true],
      ["VIEWER", "/technician", false],
      ["VIEWER", "/viewer", true],
      ["VIEWER", "/campus", true],
      ["TECHNICIAN", "/campus", true],
      ["FACILITY_MANAGER", "/viewer", true],
    ];
    for (const [role, path, allowed] of cases) {
      expect(canAccessRoute(role, path), `${role} → ${path}`).toBe(allowed);
    }
  });

  it("denies unknown paths for every role (no default-allow)", () => {
    for (const role of ROLES) {
      expect(canAccessRoute(role, "/whatever"), role).toBe(false);
      expect(canAccessRoute(role, "/admin-tools"), role).toBe(false); // prefix is not a path segment
      expect(canAccessRoute(role, ""), role).toBe(false);
    }
  });

  it("treats child routes and query-less prefixes identically", () => {
    expect(canAccessRoute("SUPER_ADMIN", "/admin/users/42")).toBe(true);
    expect(canAccessRoute("VIEWER", "/admin/users/42")).toBe(false);
    expect(canAccessRoute("SUPER_ADMIN", "/a/AC-001")).toBe(true);
  });
});
