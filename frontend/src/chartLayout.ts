/** Round axis ticks spanning [min, max] (always including zero), using 1/2/5 × 10ⁿ steps. */
export function niceTicks(min: number, max: number, targetCount = 5): number[] {
  const low = Math.min(0, min);
  const high = Math.max(0, max);
  if (low === high) return [0, 1];
  const rough = (high - low) / targetCount;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= rough)!;
  const ticks: number[] = [];
  for (let tick = Math.floor(low / step) * step; tick <= Math.ceil(high / step) * step + step / 2; tick += step) {
    ticks.push(Math.round(tick / step) * step);
  }
  return ticks;
}

/** UTC timestamps of every first-of-month in [start, end], for one axis label per month. */
export function monthStarts(start: number, end: number): number[] {
  const first = new Date(start);
  const months: number[] = [];
  for (let month = first.getUTCMonth(); ; month++) {
    const value = Date.UTC(first.getUTCFullYear(), month, 1);
    if (value > end) return months;
    if (value >= start) months.push(value);
  }
}

export type LabelPoint = { x: number; y: number; text: string };
export type PlacedLabel = LabelPoint & { labelY: number; anchor: "start" | "middle" | "end"; hidden: boolean };

const LABEL_HEIGHT = 13;
const GAP_ABOVE = 8;
const GAP_BELOW = 16;

/**
 * Places a value label above or below each point so labels don't overlap.
 *
 * Peaks get labels above and dips below; a label that would collide with an earlier
 * one flips to the other side, and is hidden when both sides collide. A label that would
 * cross `avoidY` (such as the zero line) moves to that line's far side instead.
 */
export function layoutPointLabels(
  points: LabelPoint[],
  { charWidth, top, bottom, avoidY }: { charWidth: number; top: number; bottom: number; avoidY?: number },
): PlacedLabel[] {
  const placed: { left: number; right: number; top: number; bottom: number }[] = [];
  return points.map((point, index) => {
    const anchor = index === 0 ? "start" : index === points.length - 1 ? "end" : "middle";
    const width = point.text.length * charWidth;
    const left = anchor === "start" ? point.x : anchor === "end" ? point.x - width : point.x - width / 2;
    const neighbours = [points[index - 1], points[index + 1]].filter(Boolean);
    const isDip = neighbours.length > 0 && neighbours.every((neighbour) => point.y > neighbour.y);
    const sides = isDip ? ["below", "above"] : ["above", "below"];
    for (const side of sides) {
      let baseline = side === "above" ? point.y - GAP_ABOVE : point.y + GAP_BELOW;
      if (avoidY !== undefined && baseline - LABEL_HEIGHT + 3 < avoidY && avoidY < baseline + 3) {
        baseline = side === "above" ? avoidY - 5 : avoidY + LABEL_HEIGHT;
      }
      const box = { left, right: left + width, top: baseline - LABEL_HEIGHT + 3, bottom: baseline + 3 };
      const fits = box.top >= top && box.bottom <= bottom
        && !placed.some((other) =>
          box.left < other.right && other.left < box.right && box.top < other.bottom && other.top < box.bottom);
      if (fits) {
        placed.push(box);
        return { ...point, labelY: baseline, anchor, hidden: false };
      }
    }
    return { ...point, labelY: point.y, anchor, hidden: true };
  });
}
