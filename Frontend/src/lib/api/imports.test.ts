import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, IMPORT_TIMEOUT_MS } from "./client";
import {
  describeImportFailure,
  isUncertainImportFailure,
  type ImportReport,
  uploadAssetImport,
} from "./imports";
import { apiFormData } from "./client";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, apiFormData: vi.fn(async () => ({ dry_run: true })) };
});

const TIMEOUT_MSG =
  "The import request timed out while the server was processing it. " +
  "The result may still have been committed. Verify the import before retrying.";

function apiError(code: string, message: string, status = 0): ApiError {
  return new ApiError(code, message, status, "req_test");
}

describe("isUncertainImportFailure", () => {
  it("flags only outcomes the server never confirmed (timeout / network)", () => {
    expect(isUncertainImportFailure(apiError("TIMEOUT", "The server took too long to respond."))).toBe(true);
    expect(isUncertainImportFailure(apiError("NETWORK_ERROR", "Could not reach the server."))).toBe(true);
    expect(isUncertainImportFailure(apiError("VALIDATION_ERROR", "Bad file.", 400))).toBe(false);
    expect(isUncertainImportFailure(apiError("IMPORT_FAILED", "Import failed.", 500))).toBe(false);
    expect(isUncertainImportFailure(new Error("boom"))).toBe(false);
    expect(isUncertainImportFailure(undefined)).toBe(false);
  });
});

describe("describeImportFailure", () => {
  it("uses the required timeout wording verbatim", () => {
    const text = describeImportFailure(apiError("TIMEOUT", "The server took too long to respond."));
    expect(text).toBe(TIMEOUT_MSG);
  });

  it("never claims no data was changed for an uncertain outcome", () => {
    const timeout = describeImportFailure(apiError("TIMEOUT", "The server took too long to respond."));
    const network = describeImportFailure(apiError("NETWORK_ERROR", "Could not reach the server."));
    expect(timeout).not.toContain("No data was changed");
    expect(network).not.toContain("No data was changed");
    expect(network).toContain("may still have been committed");
  });

  it("explains an in-flight identical import (409) without claiming no change", () => {
    const text = describeImportFailure(
      apiError("IMPORT_IN_PROGRESS", "An identical import is already being processed.", 409),
    );
    expect(text).toContain("still processing an identical import");
    expect(text).not.toContain("No data was changed");
  });

  it("appends the confirmation only when the backend answered (4xx/5xx envelope)", () => {
    const text = describeImportFailure(apiError("VALIDATION_ERROR", "Row 3: brand is required.", 400));
    expect(text).toBe("Row 3: brand is required. No data was changed.");
  });

  it("keeps non-ApiError failures explicitly unknown-outcome", () => {
    const text = describeImportFailure(new TypeError("undefined is not a function"));
    expect(text).toContain("unknown");
    expect(text).not.toContain("No data was changed");
  });
});

describe("ImportReport.replayed", () => {
  it("is optional, so existing reports type-check unchanged", () => {
    const report = { dry_run: false, imported: 3, failed: 0 } as Partial<ImportReport>;
    expect(report.replayed).toBeUndefined();
  });
});

describe("uploadAssetImport timeout budget (§13)", () => {
  const file = () => new File(["ac_id\nX1"], "assets.csv", { type: "text/csv" });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("gives bulk commits the longer caller-specific timeout", async () => {
    await uploadAssetImport(file(), false);
    expect(apiFormData).toHaveBeenCalledTimes(1);
    const [, , , timeoutMs] = vi.mocked(apiFormData).mock.calls[0];
    expect(timeoutMs).toBe(IMPORT_TIMEOUT_MS);
    expect(IMPORT_TIMEOUT_MS).toBeGreaterThan(15_000);
  });

  it("keeps dry runs on the normal budget (default timeout)", async () => {
    await uploadAssetImport(file(), true);
    expect(apiFormData).toHaveBeenCalledTimes(1);
    const [, , , timeoutMs] = vi.mocked(apiFormData).mock.calls[0];
    expect(timeoutMs).toBeUndefined();
  });
});
