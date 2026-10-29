import { describe, expect, it } from "vitest";
import { layoutPointLabels, monthStarts, niceTicks } from "./chartLayout";

describe("niceTicks", () => {
  it("uses round steps from zero past the maximum", () => {
    expect(niceTicks(950.00, 27950.00)).toEqual([0, 10000, 20000, 30000]);
    expect(niceTicks(569, 43748)).toEqual([0, 10000, 20000, 30000, 40000, 50000]);
  });

  it("includes zero and negative values", () => {
    expect(niceTicks(-1200, 3400)).toEqual([-2000, -1000, 0, 1000, 2000, 3000, 4000]);
  });

  it("handles a flat zero series", () => {
    expect(niceTicks(0, 0)).toEqual([0, 1]);
  });
});

describe("layoutPointLabels", () => {
  const options = { charWidth: 7, top: 0, bottom: 200 };

  it("puts peaks above and dips below their points", () => {
    const [first, dip, peak, last] = layoutPointLabels(
      [
        { x: 0, y: 100, text: "€ 1" },
        { x: 100, y: 150, text: "€ 2" },
        { x: 200, y: 50, text: "€ 3" },
        { x: 300, y: 100, text: "€ 4" },
      ],
      options,
    );
    expect(dip.labelY).toBeGreaterThan(dip.y);
    expect(peak.labelY).toBeLessThan(peak.y);
    expect([first.anchor, dip.anchor, last.anchor]).toEqual(["start", "middle", "end"]);
  });

  it("moves labels that would cross the avoided line to its far side", () => {
    const [, dip] = layoutPointLabels(
      [
        { x: 0, y: 100, text: "€ 1" },
        { x: 100, y: 190, text: "€ 2" },
        { x: 200, y: 100, text: "€ 3" },
      ],
      { ...options, bottom: 220, avoidY: 198 },
    );
    expect(dip.hidden).toBe(false);
    expect(dip.labelY - 13 + 3).toBeGreaterThan(198);
  });

  it("flips or hides labels that would overlap", () => {
    const labels = layoutPointLabels(
      [
        { x: 100, y: 100, text: "€ 12.270" },
        { x: 130, y: 100, text: "€ 12.270" },
        { x: 160, y: 100, text: "€ 12.270" },
      ],
      options,
    );
    expect(labels[0].hidden).toBe(false);
    expect(labels[1].hidden).toBe(false);
    expect(labels[1].labelY).not.toBe(labels[0].labelY);
    expect(labels[2].hidden).toBe(true);
  });
});

describe("monthStarts", () => {
  it("returns every month start in range, across a year boundary", () => {
    const months = monthStarts(Date.UTC(2025, 10, 1), Date.UTC(2026, 1, 15));
    expect(months).toEqual([Date.UTC(2025, 10, 1), Date.UTC(2025, 11, 1), Date.UTC(2026, 0, 1), Date.UTC(2026, 1, 1)]);
  });

  it("skips a partial first month", () => {
    expect(monthStarts(Date.UTC(2026, 0, 15), Date.UTC(2026, 2, 1))).toEqual([Date.UTC(2026, 1, 1), Date.UTC(2026, 2, 1)]);
  });
});
