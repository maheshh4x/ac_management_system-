import { describe, expect, it } from "vitest";

import { mergeSyncResults, type QueuedJobMutation } from "./offline-queue";

function item(cmid: string, overrides: Partial<QueuedJobMutation> = {}): QueuedJobMutation {
  return {
    client_mutation_id: cmid,
    action: "accept",
    job_id: "w1",
    payload: {},
    queued_at: 1,
    label: "Accept job",
    state: "pending",
    ...overrides,
  };
}

describe("mergeSyncResults", () => {
  it("drops items the server applied or had already seen (duplicate)", () => {
    const queue = [item("a"), item("b"), item("c")];
    const merged = mergeSyncResults(queue, [
      { client_mutation_id: "a", result: "applied" },
      { client_mutation_id: "b", result: "duplicate" },
    ]);
    expect(merged.map(i => i.client_mutation_id)).toEqual(["c"]);
    expect(merged[0].state).toBe("pending");
  });

  it("keeps rejected items visible with the server's code and message", () => {
    const queue = [item("a"), item("b")];
    const merged = mergeSyncResults(queue, [
      { client_mutation_id: "a", result: "rejected", code: "FORBIDDEN", message: "Not your job." },
      { client_mutation_id: "b", result: "applied" },
    ]);
    expect(merged).toHaveLength(1);
    expect(merged[0].client_mutation_id).toBe("a");
    expect(merged[0].state).toBe("rejected");
    expect(merged[0].reason).toBe("FORBIDDEN: Not your job.");
  });

  it("keeps items the server did not answer pending for the next attempt", () => {
    const merged = mergeSyncResults([item("a"), item("b")], []);
    expect(merged.map(i => i.client_mutation_id)).toEqual(["a", "b"]);
    expect(merged.every(i => i.state === "pending")).toBe(true);
  });

  it("re-rejected items get their reason refreshed", () => {
    const queue = [item("a", { state: "rejected", reason: "SYNC_FAILED: old" })];
    const merged = mergeSyncResults(queue, [
      { client_mutation_id: "a", result: "rejected", code: "TERMINAL_STATE", message: "Job cancelled." },
    ]);
    expect(merged[0].state).toBe("rejected");
    expect(merged[0].reason).toBe("TERMINAL_STATE: Job cancelled.");
  });
});
