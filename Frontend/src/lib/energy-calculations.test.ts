import { describe, expect, it } from "vitest";

import {
  buildMatrix,
  dailyBuckets,
  DEFAULT_LOAD_FACTOR,
  estimateSessions,
  formatHours,
  formatKwh,
  kwForAsset,
  sessionHours,
  simulateSavings,
  totalEnergy,
  type EnergySessionInput,
} from "./energy-calculations";

const NOW = new Date("2026-01-10T12:00:00Z").getTime();

function session(over: Partial<EnergySessionInput> & { id: string }): EnergySessionInput {
  return {
    ac_id: "AC-1",
    started_at: "2026-01-10T10:00:00Z",
    ended_at: "2026-01-10T11:00:00Z",
    duration_seconds: 3600,
    power_rating_kw: 1.5,
    capacity_ton: 1.5,
    manufacturer: "Daikin",
    block: "Main Block",
    department: "CSE",
    floor: "Floor 1",
    room: "101",
    ...over,
  };
}

describe("kwForAsset — rated power preferred, never invented", () => {
  it("uses the rated power when present", () => {
    expect(kwForAsset({ power_rating_kw: 1.8, capacity_ton: 1.5 })).toEqual({
      kw: 1.8,
      basis: "rated",
      modelVersion: "baseline-v1",
    });
  });

  it("falls back to tons × 1.2 only when rated power is missing, with a distinct model version", () => {
    expect(kwForAsset({ power_rating_kw: null, capacity_ton: 2 })).toEqual({
      kw: 2.4,
      basis: "capacity-derived",
      modelVersion: "baseline-v1-capacity-derived",
    });
  });

  it("does not use capacity when rated power is invalid-but-unusable", () => {
    // Rated path unusable (0) → capacity-derived, clearly distinguished.
    const out = kwForAsset({ power_rating_kw: 0, capacity_ton: 1.5 });
    expect(out.kw).toBeCloseTo(1.8, 10);
    expect(out.basis).toBe("capacity-derived");
    expect(out.modelVersion).toBe("baseline-v1-capacity-derived");
  });

  it("reports unknown instead of fabricating a value", () => {
    expect(kwForAsset({ power_rating_kw: null, capacity_ton: null })).toEqual({
      kw: null,
      basis: "unknown",
      modelVersion: null,
    });
    expect(kwForAsset({ power_rating_kw: 0, capacity_ton: -1 })).toEqual({
      kw: null,
      basis: "unknown",
      modelVersion: null,
    });
  });
});

describe("sessionHours — ledger-only runtime", () => {
  it("uses recorded durations", () => {
    expect(sessionHours(session({ id: "a" }), NOW)).toBe(1);
  });

  it("accrues open sessions to now, never into the future", () => {
    expect(
      sessionHours(session({ id: "a", ended_at: null, duration_seconds: null }), NOW),
    ).toBe(2);
    expect(sessionHours(session({ id: "a", started_at: null, duration_seconds: null }), NOW)).toBe(0);
  });
});

describe("estimateSessions — missing inputs excluded, never zeroed", () => {
  it("estimates with the explicit average load factor", () => {
    const [row] = estimateSessions([session({ id: "a" })], 0.5, NOW);
    expect(row.kwh).toBe(1.5 * 1 * 0.5);
    expect(row.excludedReason).toBeNull();
  });

  it("excludes sessions without power data and says why", () => {
    const [row] = estimateSessions(
      [session({ id: "a", power_rating_kw: null, capacity_ton: null })],
      DEFAULT_LOAD_FACTOR,
      NOW,
    );
    expect(row.kwh).toBeNull();
    expect(row.excludedReason).toContain("no power rating");
    expect(row.basis).toBe("unknown");
  });

  it("labels capacity-derived estimates distinctly from rated ones", () => {
    const [rated] = estimateSessions([session({ id: "a", power_rating_kw: 1.5 })], 0.75, NOW);
    const [derived] = estimateSessions(
      [session({ id: "b", power_rating_kw: null, capacity_ton: 1.25 })],
      0.75,
      NOW,
    );
    expect(rated.basis).toBe("rated");
    expect(rated.modelVersion).toBe("baseline-v1");
    expect(derived.basis).toBe("capacity-derived");
    expect(derived.modelVersion).toBe("baseline-v1-capacity-derived");
    // Same numeric path (1.25 t × 1.2 = 1.5 kW), different provenance.
    expect(derived.kwh).toBe(1.5 * 1 * 0.75);
    expect(derived.kwh).toBe(rated.kwh);
  });

  it("never yields NaN when the load factor is not a finite number", () => {
    const [row] = estimateSessions([session({ id: "a" })], Number.NaN, NOW);
    expect(row.kwh).not.toBeNaN();
    expect(row.kwh).toBe(1.5 * 1 * 0.7);
  });
});

describe("totalEnergy — no double counting", () => {
  it("sums once across sessions and counts open separately", () => {
    const rows = estimateSessions(
      [session({ id: "a" }), session({ id: "b", ac_id: "AC-2", duration_seconds: 1800 })],
      1,
      NOW,
    );
    const totals = totalEnergy(rows, new Set(["a"]), 1);
    expect(totals.sessionCount).toBe(2);
    expect(totals.openCount).toBe(1);
    expect(totals.runtimeHours).toBe(1.5);
    expect(totals.estimatedKwh).toBe(1.5 * 1 + 1.5 * 0.5);
    expect(totals.excludedSessions).toBe(0);
  });
});

describe("dailyBuckets — zero-filled, never backfilled", () => {
  it("emits every day in range including empty ones", () => {
    const rows = estimateSessions([session({ id: "a" })], 1, NOW);
    const byId = new Map([["a", session({ id: "a" })]]);
    const buckets = dailyBuckets(rows, byId, "2026-01-09", "2026-01-11");
    expect(buckets.map((b) => b.date)).toEqual(["2026-01-09", "2026-01-10", "2026-01-11"]);
    expect(buckets[0]).toMatchObject({ runtimeHours: 0, estimatedKwh: 0, sessions: 0 });
    expect(buckets[1]).toMatchObject({ runtimeHours: 1, sessions: 1 });
  });

  it("rejects inverted ranges", () => {
    expect(dailyBuckets([], new Map(), "2026-01-11", "2026-01-09")).toEqual([]);
  });
});

describe("buildMatrix — inventory-joined comparisons", () => {
  it("joins units with session aggregates and tracks ON per group", () => {
    const rows = estimateSessions(
      [
        session({ id: "a", ac_id: "AC-1" }),
        session({ id: "b", ac_id: "AC-2", duration_seconds: null, ended_at: null }),
      ],
      1,
      NOW,
    );
    const byId = new Map([
      ["a", session({ id: "a", block: "Main Block" })],
      ["b", session({ id: "b", ac_id: "AC-2", block: "Annex" })],
    ]);
    const matrix = buildMatrix(
      new Map([
        ["Main Block", 5],
        ["Annex", 2],
      ]),
      rows,
      (id) => byId.get(id)?.block ?? null,
      new Set(["b"]),
    );
    const main = matrix.find((r) => r.key === "Main Block")!;
    expect(main).toMatchObject({ units: 5, onUnits: 0, sessionsTotal: 1 });
    const annex = matrix.find((r) => r.key === "Annex")!;
    expect(annex.onUnits).toBe(1);
  });
});

describe("simulateSavings — labeled arithmetic only", () => {
  it("scales the baseline and reports the exclusion share", () => {
    const result = simulateSavings(100, 20, 0.7, 5, 50);
    expect(result).toMatchObject({
      baselineKwh: 100,
      simulatedKwh: 80,
      avoidedKwh: 20,
      reductionPct: 20,
    });
    expect(result.excludedKwhShare).toContain("10.0%");
  });

  it("clamps the reduction to 0–100", () => {
    expect(simulateSavings(100, 150, 0.7, 0, 10).simulatedKwh).toBe(0);
    expect(simulateSavings(100, -5, 0.7, 0, 10).simulatedKwh).toBe(100);
  });
});

describe("formatters", () => {
  it("never renders NaN for missing values", () => {
    expect(formatKwh(null)).toBe("—");
    expect(formatKwh(Number.NaN)).toBe("—");
    expect(formatHours(undefined)).toBe("—");
    expect(formatHours(0.5)).toBe("30 min");
    expect(formatHours(2)).toBe("2 h");
  });
});
