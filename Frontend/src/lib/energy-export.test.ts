import { describe, expect, it } from "vitest";

import { buildEnergyCsv, toCsvCell } from "./energy-export";

describe("energy CSV export", () => {
  it("quotes fields containing commas, quotes or newlines", () => {
    expect(toCsvCell("plain")).toBe("plain");
    expect(toCsvCell("a,b")).toBe('"a,b"');
    expect(toCsvCell('say "hi"')).toBe('"say ""hi"""');
    expect(toCsvCell(null)).toBe("");
    expect(toCsvCell(42)).toBe("42");
  });

  it("builds a well-formed document with a header row", () => {
    const csv = buildEnergyCsv(
      ["block", "units", "kwh"],
      [
        ["Main Block", 5, 12.5],
        ["Annex, East", 2, null],
      ],
    );
    expect(csv).toBe("block,units,kwh\nMain Block,5,12.5\n\"Annex, East\",2,\n");
  });
});
