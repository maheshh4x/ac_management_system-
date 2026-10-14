/**
 * MyReportsView + IssueApprovals tests (Features B/C): reporters see their
 * own statuses (incl. rejection reasons); the admin queue approves with
 * confirmation and requires a rejection reason.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MyReportsView } from "./MyReportsView";
import { IssueApprovals } from "./IssueApprovals";

const mutateMine = vi.fn();
const detailMutate = vi.fn();

vi.mock("@/lib/api/issue-reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/issue-reports")>();
  return {
    ...actual,
    useMyReports: () => ({
      data: {
        reports: [
          {
            id: "r-1",
            report_code: "IR-AAAA1111",
            ac_id: "AC-1",
            reporter_id: "u-1",
            category: "WATER_LEAKAGE",
            description: "Water dripping from indoor unit.",
            priority: "HIGH",
            location_text: null,
            asset_location_snapshot: "Main Block / Floor 1 / Lab 1",
            status: "REJECTED",
            decided_by: "admin-1",
            decided_at: "2026-10-10T10:00:00+00:00",
            rejection_reason: "Already covered by job WO-9.",
            work_order_id: null,
            created_at: "2026-10-09T10:00:00+00:00",
            updated_at: "2026-10-10T10:00:00+00:00",
          },
        ],
        pagination: { page: 1, page_size: 100, total: 1 },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
    useIssueReport: (id: string | null) => ({
      data:
        id === "r-1"
          ? {
              id: "r-1",
              report_code: "IR-AAAA1111",
              ac_id: "AC-1",
              category: "WATER_LEAKAGE",
              description: "Water dripping from indoor unit.",
              priority: "HIGH",
              location_text: null,
              asset_location_snapshot: "Main Block / Floor 1 / Lab 1",
              status: "REJECTED",
              rejection_reason: "Already covered by job WO-9.",
              work_order: null,
              decided_by_profile: { id: "admin-1", name: "Admin", role: "SUPER_ADMIN" },
              decided_at: "2026-10-10T10:00:00+00:00",
            }
          : null,
      isLoading: false,
      isError: false,
    }),
    usePendingReports: () => ({
      data: {
        reports: [
          {
            id: "p-1",
            report_code: "IR-BBBB2222",
            ac_id: "AC-2",
            asset_code: "AC-2",
            reporter_name: "Tech One",
            category: "AC_NOT_COOLING",
            description: "Not cooling since yesterday.",
            priority: "HIGH",
            status: "PENDING_APPROVAL",
            work_order_id: null,
            created_at: "2026-10-11T10:00:00+00:00",
          },
        ],
        pagination: { page: 1, page_size: 100, total: 1 },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
    useApproveReport: () => ({ mutate: mutateMine, isPending: false }),
    useRejectReport: () => ({ mutate: detailMutate, isPending: false }),
    useAllReports: () => ({
      data: { reports: [], pagination: { page: 1, page_size: 100, total: 0 } },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MyReportsView", () => {
  it("shows the report status and reveals the rejection reason on demand", async () => {
    render(<MyReportsView />);

    expect(await screen.findByText("IR-AAAA1111")).toBeTruthy();
    expect(screen.getByText("Rejected")).toBeTruthy();
    fireEvent.click(screen.getByText("View details"));
    expect(await screen.findByText(/Already covered by job WO-9/)).toBeTruthy();
    expect(screen.getByText(/Main Block \/ Floor 1 \/ Lab 1/)).toBeTruthy();
  });
});

describe("IssueApprovals", () => {
  it("confirms before approving and reports the queued job", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mutateMine.mockImplementationOnce((id: string, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ id: "wo-1", report_id: id, report_code: "IR-BBBB2222", status: "SCHEDULED", already_processed: false }),
    );
    render(<IssueApprovals />);

    expect(await screen.findByText("IR-BBBB2222")).toBeTruthy();
    fireEvent.click(screen.getByText("Approve"));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText(/technician job wo-1 queued/)).toBeTruthy());
    confirm.mockRestore();
  });

  it("requires a reason before rejecting", async () => {
    render(<IssueApprovals />);

    expect(await screen.findByText("IR-BBBB2222")).toBeTruthy();
    fireEvent.click(screen.getByText("Reject"));
    fireEvent.click(screen.getByTitle("Confirm rejection"));
    expect(await screen.findByText("A rejection reason is required.")).toBeTruthy();
    expect(detailMutate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Reason (required)"), { target: { value: "Duplicate." } });
    detailMutate.mockImplementationOnce((input: unknown, opts: { onSuccess: () => void }) => opts.onSuccess());
    fireEvent.click(screen.getByTitle("Confirm rejection"));
    await waitFor(() => expect(detailMutate).toHaveBeenCalledTimes(1));
  });
});
