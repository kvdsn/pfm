import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BalanceChart } from "./BalanceChart";
import type { Forecast, ForecastMonth, ForecastVsActual } from "./types";

// jsdom has no layout: report a fixed size to the chart.
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    constructor(private callback: (entries: unknown[]) => void) {}
    observe() { this.callback([{ contentRect: { width: 900, height: 400 } }]); }
    disconnect() {}
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const month = (key: string, closing: number, flags: ForecastMonth["flags"] = []): ForecastMonth => ({
  month: key, partial: false, lines: [], actual: { income: "0", expense: "0" }, income: "0", expense: "0", net: "0",
  closing: closing.toFixed(2), low: (closing - 500).toFixed(2), high: (closing + 700).toFixed(2), flags,
});
const forecast = {
  accounts: [{ name: "KBC", balance: "1000.00", as_of: "2026-10-24" }],
  months: [month("2026-10", 1200), month("2026-11", 1500, ["transfer_short"]), month("2026-12", 900), month("2027-01", 800)],
} as Forecast;
const history = { points: [{ date: "2026-09-30", balance: "1000.00" }, { date: "2026-10-20", balance: "1100.00" }] };

describe("BalanceChart forecast", () => {
  it("draws the dashed line and the band after the last actual point", () => {
    const { container } = render(<BalanceChart history={history} forecast={forecast} year="2026" />);
    expect(container.querySelector(".forecast-line")).toBeInTheDocument();
    expect(container.querySelector(".forecast-band")).toBeInTheDocument();
    // October, November and December of 2026; January 2027 belongs to the next year.
    expect(container.querySelectorAll(".forecast-point")).toHaveLength(3);
    expect(container.querySelectorAll(".forecast-shortfall")).toHaveLength(1);
    expect(screen.getByText(/Dashed: forecast, with its range from the backtest/)).toHaveTextContent("KBC 2026-10-24");
  });

  it("draws a forecast-only year without actual points", () => {
    const { container } = render(<BalanceChart history={null} forecast={forecast} year="2027" />);
    expect(container.querySelector(".forecast-line")).toBeInTheDocument();
    expect(container.querySelector(".balance-line")).toBeNull();
    expect(container.querySelectorAll(".forecast-point")).toHaveLength(1);
  });

  it("is unchanged without a forecast", () => {
    const { container } = render(<BalanceChart history={history} />);
    expect(container.querySelector(".forecast-line")).toBeNull();
    expect(container.querySelector(".balance-line")).toBeInTheDocument();
    expect(screen.queryByText(/Dashed/)).not.toBeInTheDocument();
  });
});

describe("BalanceChart forecast vs reality", () => {
  const vs = {
    months: [{
      month: "2026-09", snapshot_date: "2026-09-01", status: "complete",
      balance: { forecast: "1300.00", low: "900.00", high: "1700.00", actual: "1000.00", difference: "-300.00", miss: false },
    }],
  } as unknown as ForecastVsActual;

  it("marks each comparison month's predicted balance, with actual, predicted and range on hover", () => {
    const { container } = render(<BalanceChart history={history} forecast={forecast} vsActual={vs} year="2026" />);
    const markers = container.querySelectorAll(".vs-marker");
    expect(markers).toHaveLength(1);
    const title = markers[0].querySelector("title")!.textContent!;
    expect(title).toContain("actual € 1.000,00");
    expect(title).toContain("predicted € 1.300,00");
    expect(title).toContain("€ 900,00 – € 1.700,00");
  });

  it("draws no marker without a comparison or for another year", () => {
    const { container, rerender } = render(<BalanceChart history={history} forecast={forecast} year="2026" />);
    expect(container.querySelector(".vs-marker")).toBeNull();
    rerender(<BalanceChart history={history} forecast={forecast} vsActual={vs} year="2027" />);
    expect(container.querySelector(".vs-marker")).toBeNull();
  });
});
