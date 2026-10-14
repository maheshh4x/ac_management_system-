/**
 * ReportIssueModal tests (Feature B): validation rejects an empty
 * description, submission shows the report reference with pending status,
 * and server errors preserve the form input.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportIssueModal } from "./ReportIssueModal";
import { ApiError } from "@/lib/api/client";

const mutate = vi.fn();

vi.mock("@/lib/api/issue-reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/issue-reports")>();
  return {
    ...actual,
    useCreateIssueReport: () => ({
      mutate,
      isPending: false,
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function fillDescription(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/What is wrong/), { target: { value: text } });
}

describe("ReportIssueModal", () => {
  it("rejects an empty description without calling the API", () => {
    render(<ReportIssueModal acId="AC-1" onClose={() => undefined} />);

    fillDescription("   ");
    fireEvent.click(screen.getByText("File report"));

    expect(screen.getByText("Please describe the issue.")).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("submits with an idempotency key and shows the pending confirmation", async () => {
    mutate.mockImplementationOnce((_input: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ report_code: "IR-ABC123", status: "PENDING_APPROVAL" }),
    );
    render(<ReportIssueModal acId="AC-1" onClose={() => undefined} />);

    fillDescription("AC blows warm air.");
    fireEvent.click(screen.getByText("File report"));

    await waitFor(() => expect(screen.getByText("Report filed")).toBeTruthy());
    expect(screen.getByText("IR-ABC123")).toBeTruthy();
    expect(screen.getAllByText(/Pending approval/).length).toBeGreaterThanOrEqual(1);
    const [input] = mutate.mock.calls[0];
    expect(input).toMatchObject({ ac_id: "AC-1", description: "AC blows warm air." });
    expect(typeof input.idempotency_key).toBe("string");
  });

  it("preserves input and explains an unknown asset", async () => {
    mutate.mockImplementationOnce(
      (_input: unknown, opts: { onError: (e: unknown) => void }) =>
        opts.onError(new ApiError("ASSET_NOT_FOUND", "Asset not found.", 404, null)),
    );
    render(<ReportIssueModal acId="AC-1" onClose={() => undefined} allowAssetEntry />);

    fillDescription("Water leaking.");
    fireEvent.click(screen.getByText("File report"));

    await waitFor(() => expect(screen.getByText(/No asset matches/)).toBeTruthy());
    // Input preserved for correction.
    expect((screen.getByPlaceholderText(/What is wrong/) as HTMLTextAreaElement).value).toBe("Water leaking.");
  });
});
