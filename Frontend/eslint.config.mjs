import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Test scaffolding may use localStorage freely.
    "**/*.test.ts",
    "**/*.test.tsx",
    "src/dev/**",
    "vitest.setup.ts",
  ]),
  {
    rules: {
      // Phase 2 non-negotiables: no direct DB driver, no localStorage as a
      // business-data store (only src/lib/storage may touch it).
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "pg",
              message: "Direct DB access is forbidden in the frontend. Use src/lib/api.",
            },
          ],
        },
      ],
      "no-restricted-globals": [
        "error",
        {
          name: "localStorage",
          message:
            "Business data must come from the API. Persistence helpers live in src/lib/storage only.",
        },
      ],
    },
  },
  // localStorage allowed inside the designated storage module. Additions to
  // this list are forbidden; removals happen as each service is replaced by
  // the API (02-api-contract.md "Pages to kill the mock data in").
  // Phase 3 removed: ac-data-service.ts, useCampusManagement.ts.
  // Phase 4 removed: maintenance-data-service.ts, technician-data-service.ts.
  // Phase 5 removed: movement-data-service.ts.
  // Phase 6: qr-service.ts was never here (in-memory mock, lint-debt list).
  // Phase 7 removed: user-service.ts (last localStorage business-data store).
  {
    files: ["src/lib/storage/**"],
    rules: { "no-restricted-globals": "off" },
  },
  // Pre-existing lint debt in code that Phases 3–7 rewrite (recorded in the
  // Phase 2 report; kept as warnings so NEW code stays strict while the old
  // mock-driven files don't block CI until their phase replaces them).
  // Phase 6 removed: src/lib/qr-service.ts (rewritten against the API).
  // Phase 7 removed: src/app/(portal)/admin/dashboard/page.tsx and both
  // maintenance pages (all now clean).
  {
    files: [
      "src/components/campus/**",
      "src/components/features/**",
      "src/components/landing/**",
    ],
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "react/no-unescaped-entities": "warn",
      "react-hooks/rules-of-hooks": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/immutability": "warn",
      "prefer-const": "warn",
    },
  },
]);

export default eslintConfig;
