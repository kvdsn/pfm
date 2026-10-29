import { describe, expect, it } from "vitest";
import { balanceText, forecastOnlyYears, lineFlow, linesText, projectedCategories, tipMoney } from "./forecast";
import type { Category, Forecast, ForecastLine, ForecastMonth } from "./types";

const categories: Category[] = [
  { id: 1, name: "Salary", rule_flow_type: null, is_income: true },
  { id: 2, name: "Rent", rule_flow_type: null },
  { id: 9, name: "Uncategorized", rule_flow_type: null },
];
const line = (category_id: number, category: string, amount: string, source: ForecastLine["source"] = "baseline", detail = "Median of the last 6 months"): ForecastLine => ({
  category_id, category, amount, source, date: null, detail,
});
const month = (lines: ForecastLine[], extra: Partial<ForecastMonth> = {}): ForecastMonth => ({
  month: "2026-07", partial: false, lines, actual: { income: "0.00", expense: "0.00" }, income: "0.00", expense: "0.00",
  net: "0.00", closing: "30000.00", low: "29500.00", high: "31800.00", flags: [], ...extra,
});

describe("lineFlow", () => {
  it("puts income categories in income, Uncategorized by sign and the rest in expenses", () => {
    expect(lineFlow(categories[0], "Salary", -5)).toBe("income");
    expect(lineFlow(categories[2], "Uncategorized", 5)).toBe("income");
    expect(lineFlow(categories[2], "Uncategorized", -5)).toBe("expense");
    expect(lineFlow(categories[1], "Rent", 5)).toBe("expense");
  });
});

describe("projectedCategories", () => {
  it("sums a category's lines in the table's sign convention", () => {
    const result = projectedCategories(month([
      line(2, "Rent", "-600.00"), line(2, "Rent", "100.00"), line(1, "Salary", "1200.00"), line(9, "Uncategorized", "-5.50"),
    ]), categories);
    expect(result.get("2\u0000expense")?.amount).toBe(500);
    expect(result.get("2\u0000expense")?.lines).toHaveLength(2);
    expect(result.get("1\u0000income")?.amount).toBe(1200);
    // Uncategorized has no id, as in the report.
    expect(result.get("Uncategorized\u0000expense")).toMatchObject({ category_id: null, amount: 5.5 });
  });
});

describe("tooltips", () => {
  it("writes a line with its source and a real minus sign", () => {
    expect(linesText([line(2, "Rent", "-410.00")])).toBe("Baseline (median of the last 6 months): −€410");
    expect(linesText([line(1, "Consulting", "21296.00", "consulting", "Sep 2026: 22 days × €800")]))
      .toBe("Consulting, Sep 2026: 22 days × €800: €21.296");
  });

  it("caps a long list", () => {
    expect(linesText(Array.from({ length: 14 }, () => line(2, "Rent", "-1.00")), 12).split("\n")).toHaveLength(13);
  });

  it("gives the closing balance with its range, and the transfer warning", () => {
    expect(balanceText(month([]))).toBe("Projected €30.000 (€29.500 – €31.800)");
    expect(balanceText(month([], { flags: ["transfer_short"] }))).toContain("Transfer short");
    expect(tipMoney(-1234.4)).toBe("−€1.234");
  });
});

describe("forecastOnlyYears", () => {
  it("lists the forecast's years that the report lacks", () => {
    const forecast = { months: [month([]), month([], { month: "2027-01" })] } as Forecast;
    expect(forecastOnlyYears(forecast, ["2025", "2026"])).toEqual(["2027"]);
    expect(forecastOnlyYears(null, [])).toEqual([]);
  });
});
