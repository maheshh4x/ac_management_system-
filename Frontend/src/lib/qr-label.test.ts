/**
 * Branded QR label tests (§10 frontend): the model carries every required
 * field verbatim with blanks omitted (never fabricated), long text wraps
 * instead of clipping, and the renderer draws brand/rows/QR/footer with a
 * quiet zone at print resolution.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildQrLabelModel,
  downloadQrLabel,
  renderQrLabelPng,
  wrapLabelText,
  type QrLabelFields,
} from "./qr-label";

const FIELDS: QrLabelFields = {
  assetId: "AC-LOC-TEST-1",
  make: "Daikin",
  model: "FTKM50",
  college: "National Institute of Technical Teachers Training and Research",
  block: "Main Academic Block",
  department: "Administration",
  floor: "Floor 1",
  room: "ADMIN OFFICE",
};

/** Deterministic measure stub: 10 px per character. */
const measure = (maxWidth: number) => ({
  measureText: (text: string) => ({ width: text.length * 10 }),
  maxWidth,
});

describe("buildQrLabelModel", () => {
  it("carries branding, identifier, every available dimension and the footer", () => {
    const model = buildQrLabelModel(FIELDS);
    expect(model.brand).toBe("NITTTR ACMS");
    expect(model.assetId).toBe("AC-LOC-TEST-1");
    expect(model.subtitle).toBe("Daikin FTKM50");
    expect(model.rows).toEqual([
      { label: "College", value: "National Institute of Technical Teachers Training and Research" },
      { label: "Block", value: "Main Academic Block" },
      { label: "Department", value: "Administration" },
      { label: "Location", value: "Floor 1 / ADMIN OFFICE" },
    ]);
    expect(model.footer).toBe("Scan to view asset details");
  });

  it("omits unavailable dimensions instead of fabricating them", () => {
    const model = buildQrLabelModel({ assetId: "X-1", block: "Phantom Block" });
    expect(model.rows).toEqual([{ label: "Block", value: "Phantom Block" }]);
    expect(model.subtitle).toBeNull();
  });

  it("treats blanks and placeholders as missing", () => {
    const model = buildQrLabelModel({
      assetId: "  X-2  ",
      college: "   ",
      block: "—",
      department: null,
    });
    expect(model.assetId).toBe("X-2");
    expect(model.rows).toEqual([]);
  });
});

describe("wrapLabelText", () => {
  it("keeps short text on one line", () => {
    expect(wrapLabelText(measure(500), "Main Academic Block", 500)).toEqual(["Main Academic Block"]);
  });

  it("wraps long names onto two lines without clipping words", () => {
    const lines = wrapLabelText(
      measure(0),
      "National Institute of Technical Teachers Training and Research",
      300,
    );
    expect(lines.length).toBeLessThanOrEqual(2);
    expect(lines.join(" ")).toContain("National Institute");
    expect(lines[1]).toMatch(/…$/);
  });

  it("ellipsizes a single unbreakable token wider than the column", () => {
    const lines = wrapLabelText(measure(0), "A".repeat(100), 100);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatch(/…$/);
    expect(lines[0].length).toBeLessThan(100);
  });
});

interface RecordedCall {
  method: string;
  args: unknown[];
}

function stubCanvas() {
  const calls: RecordedCall[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
    fillRect: (...args: unknown[]) => void calls.push({ method: "fillRect", args }),
    strokeRect: (...args: unknown[]) => void calls.push({ method: "strokeRect", args }),
    fillText: (...args: unknown[]) => void calls.push({ method: "fillText", args }),
    measureText: (text: string) => ({ width: text.length * 10 }),
    drawImage: (...args: unknown[]) => void calls.push({ method: "drawImage", args }),
  };
  const canvases: Array<{ width: number; height: number }> = [];
  const originalCreateElement = document.createElement.bind(document);
  const createElement = vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
    if (tag !== "canvas") {
      return originalCreateElement(tag);
    }
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => ctx,
      toBlob: (callback: (blob: Blob | null) => void) => {
        callback(new Blob(["png"], { type: "image/png" }));
      },
    };
    canvases.push(canvas);
    return canvas;
  }) as typeof document.createElement);
  const bitmap = { close: vi.fn(), width: 200, height: 200 };
  vi.stubGlobal("createImageBitmap", vi.fn(async () => bitmap));
  return { calls, canvases, createElement, bitmap };
}

function texts(calls: RecordedCall[]): string[] {
  return calls.filter((c) => c.method === "fillText").map((c) => String(c.args[0]));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("renderQrLabelPng", () => {
  it("draws branding, identifier, rows, QR with quiet zone, and footer", async () => {
    const { calls, canvases } = stubCanvas();
    const blob = await renderQrLabelPng(buildQrLabelModel(FIELDS), new Blob(["qr"]));

    expect(blob.type).toBe("image/png");
    const drawn = texts(calls);
    expect(drawn).toContain("NITTTR ACMS");
    expect(drawn.some((t) => t.includes("AC-LOC-TEST-1"))).toBe(true);
    // The long college name wraps (never clips): first line starts the
    // text, the overflow line carries the remainder with an ellipsis.
    expect(drawn.some((t) => t.startsWith("National Institute"))).toBe(true);
    expect(drawn.some((t) => t.includes("Training and Research") || t.endsWith("…"))).toBe(true);
    expect(drawn).toContain("Main Academic Block");
    expect(drawn).toContain("Administration");
    expect(drawn).toContain("Floor 1 / ADMIN OFFICE");
    expect(drawn).toContain("Scan to view asset details");

    // Exactly one QR draw, offset by the quiet zone at print size.
    const draws = calls.filter((c) => c.method === "drawImage");
    expect(draws).toHaveLength(1);
    expect(draws[0].args.slice(1)).toEqual([250, expect.any(Number), 400, 400]);

    // Print resolution card, taller with more rows.
    expect(canvases[0].width).toBe(900);
    const tall = canvases[0].height;
    const { canvases: short } = stubCanvas();
    await renderQrLabelPng(buildQrLabelModel({ assetId: "X-1" }), new Blob(["qr"]));
    expect(short[short.length - 1].height).toBeLessThan(tall);
  });

  it("renders special characters without throwing", async () => {
    stubCanvas();
    const blob = await renderQrLabelPng(
      buildQrLabelModel({
        assetId: "AC-é-1",
        college: "Maha kavi Bharathiyar Block & Salle d’attente",
        block: "JAMBULINGAM GUEST HOUSE",
      }),
      new Blob(["qr"]),
    );
    expect(blob.type).toBe("image/png");
  });
});

describe("downloadQrLabel", () => {
  it("saves the rendered label under the requested filename", async () => {
    stubCanvas();
    const createObjectURL = vi.fn(() => "blob:qr-label");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const clicked: string[] = [];
    const anchor = document.createElement("a");
    vi.spyOn(anchor, "click").mockImplementation(() => void clicked.push(anchor.download));
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
      if (tag === "a") return anchor;
      return originalCreateElement(tag);
    }) as typeof document.createElement);

    await downloadQrLabel(FIELDS, new Blob(["qr"]), "QR_AC-LOC-TEST-1.png");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clicked).toEqual(["QR_AC-LOC-TEST-1.png"]);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:qr-label");
  });
});
