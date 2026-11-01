import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForecastVsReality } from "./ForecastVsReality";
import type { ForecastVsActual, VsMiss } from "./types";

afterEach(cleanup);

const miss = (overrides: Partial<VsMiss> = {}): VsMiss => ({
  month: "2026-11", category_id: 12, category: "Groceries", flow: "expense", forecast: "564.00", actual: "612.00",
  difference: "48.00", hint: "baseline_low", recurring_id: null, ...overrides,
});
const data = (overrides: Partial<ForecastVsActual> = {}): ForecastVsActual => ({
  account_type: "personal", today: "2026-12-05", months: [{
    month: "2026-11", snapshot_date: "2026-11-01", status: "complete",
    income: { forecast: "3200.00", actual: "3200.00", difference: "0.00" },
    expense: { forecast: "2950.00", actual: "3120.50", difference: "170.50" },
    balance: { forecast: "2000.00", low: "1700.00", high: "2400.00", actual: "1850.00", difference: "-150.00", miss: false },
    categories: [],
  }],
  pace: {
    month: "2026-12", through: "2026-12-05", elapsed: 0.161,
    income: { expected: "0.00", actual: "0.00", difference: "0.00" },
    expense: { expected: "640.00", actual: "725.00", difference: "85.00" },
    categories: [
      { category_id: 12, category: "Groceries", flow: "expense", expected: "92.00", actual: "130.00", difference: "38.00", ahead: true },
      { category_id: 13, category: "Fun", flow: "expense", expected: "10.00", actual: "9.00", difference: "-1.00", ahead: false },
    ],
  },
  summary: { months_tracked: 1, typical_balance_miss: "150.00" },
  misses: [miss()],
  ...overrides,
});
const renderPanel = (vs: ForecastVsActual = data()) => {
  const onOpenRecurring = vi.fn();
  render(<ForecastVsReality vsActual={vs} onOpenRecurring={onOpenRecurring} />);
  return { onOpenRecurring, panel: within(screen.getByRole("region", { name: "Forecast vs reality" })) };
};

describe("ForecastVsReality", () => {
  it("shows the summary tiles and the categories running ahead", () => {
    const { panel } = renderPanel();
    expect(panel.getByLabelText("Typical month-end miss")).toHaveTextContent("€ 150,00");
    const pace = panel.getByLabelText("This month's pace");
    expect(pace).toHaveTextContent("+ € 85,00");
    expect(pace.querySelector(".vs-tile-value")).toHaveClass("bad");
    expect(panel.getByLabelText("Months tracked")).toHaveTextContent("1");
    const ahead = panel.getByRole("list", { name: "Categories running ahead" });
    expect(within(ahead).getByText(/Groceries/)).toHaveTextContent("+ € 38,00");
    expect(within(ahead).queryByText(/Fun/)).not.toBeInTheDocument();
  });

  it("lists the biggest misses with colored differences", () => {
    const { panel } = renderPanel(data({ misses: [miss(), miss({ category_id: 3, category: "Salary", flow: "income", forecast: "3000.00", actual: "2900.00", difference: "-100.00", hint: null })] }));
    const rows = within(panel.getByRole("table", { name: "Biggest misses" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Groceries");
    expect(rows[0]).toHaveTextContent("€ 564,00");
    expect(rows[0]).toHaveTextContent("€ 612,00");
    const overspend = within(rows[0]).getByText("+ € 48,00");
    expect(overspend).toHaveClass("bad");
    // Less income than forecast is bad too.
    expect(within(rows[1]).getByText("− € 100,00")).toHaveClass("bad");
    expect(within(rows[1]).queryByRole("button")).not.toBeInTheDocument();
  });

  it("links amount_changed to the recurring item, make_recurring to Recurring, and baseline hints to Baselines", () => {
    const scroll = vi.fn();
    const baselines = document.createElement("section");
    baselines.setAttribute("aria-label", "Baselines");
    baselines.scrollIntoView = scroll;
    document.body.append(baselines);
    const { onOpenRecurring, panel } = renderPanel(data({ misses: [
      miss({ category: "Internet", hint: "amount_changed", recurring_id: 7 }),
      miss({ category: "Gym", hint: "make_recurring" }),
      miss({ category: "Fuel", hint: "baseline_high" }),
      miss({ category: "Bike", hint: "one_off" }),
    ] }));
    fireEvent.click(panel.getByRole("button", { name: "Amount changed" }));
    expect(onOpenRecurring).toHaveBeenLastCalledWith(7);
    fireEvent.click(panel.getByRole("button", { name: "Make recurring?" }));
    expect(onOpenRecurring).toHaveBeenLastCalledWith(null);
    fireEvent.click(panel.getByRole("button", { name: "Baseline too high" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(onOpenRecurring).toHaveBeenCalledTimes(2);
    // A one-off has no link.
    expect(panel.getByText("One-off")).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "One-off" })).not.toBeInTheDocument();
    baselines.remove();
  });

  it("explains the empty state before the first comparison", () => {
    const { panel } = renderPanel(data({ months: [], pace: null, misses: [], summary: { months_tracked: 0, typical_balance_miss: null } }));
    expect(panel.getByText(/first comparison appears after the first full month with a snapshot/)).toBeInTheDocument();
    expect(panel.queryByLabelText("Months tracked")).not.toBeInTheDocument();
  });

  it("shows dashes without a typical miss or a pace", () => {
    const { panel } = renderPanel(data({ pace: null, summary: { months_tracked: 1, typical_balance_miss: null } }));
    expect(panel.getByLabelText("Typical month-end miss")).toHaveTextContent("—");
    expect(panel.getByLabelText("This month's pace")).toHaveTextContent("—");
    expect(panel.queryByText("Running ahead this month")).not.toBeInTheDocument();
  });
});
