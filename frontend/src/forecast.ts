import type { Category, Forecast, ForecastLine, ForecastMonth } from "./types";
import { overviewNumber } from "./format";

const UNCATEGORIZED = "Uncategorized";
export type Flow = "income" | "expense";

/** The explanation of a `transfer_short` month. */
export const TRANSFER_SHORT_TEXT = "Transfer short: Business cannot top Personal up to its minimum balance this month";

/** A euro amount in whole euros for tooltips, with a real minus sign: "−€410". */
export const tipMoney = (value: number) => `${value < 0 ? "−" : ""}€${overviewNumber.format(Math.abs(Math.round(value)))}`;

const cents = (value: string | number) => Math.round(Number(value) * 100);

/** The group a projected amount is reported in; the twin of `report_flow_type` in the backend. */
export function lineFlow(category: Category | undefined, name: string, amount: number): Flow {
  if (category?.is_income) return "income";
  if (name === UNCATEGORIZED) return amount > 0 ? "income" : "expense";
  return "expense";
}

/** A category's projected lines in one month and flow, with their sum in the year table's sign convention. */
export type ProjectedCategory = {
  /** Null for Uncategorized, as in the report. */
  category_id: number | null;
  name: string;
  flow: Flow;
  /** Income counts signed amounts, expenses negated ones, so a refund lowers an expense. */
  amount: number;
  lines: ForecastLine[];
};

/** The month's lines grouped per category and flow, keyed by `projectedKey`. */
export function projectedCategories(month: ForecastMonth, categories: Category[]): Map<string, ProjectedCategory> {
  const result = new Map<string, ProjectedCategory & { cents: number }>();
  for (const line of month.lines) {
    const amount = cents(line.amount);
    const flow = lineFlow(categories.find((category) => category.id === line.category_id), line.category, amount);
    const id = line.category === UNCATEGORIZED ? null : line.category_id;
    const key = projectedKey(id, line.category, flow);
    let entry = result.get(key);
    if (!entry) {
      entry = { category_id: id, name: line.category, flow, amount: 0, cents: 0, lines: [] };
      result.set(key, entry);
    }
    entry.cents += flow === "income" ? amount : -amount;
    entry.amount = entry.cents / 100;
    entry.lines.push(line);
  }
  return result;
}

export const projectedKey = (id: number | null, name: string, flow: Flow) => `${id ?? name}\u0000${flow}`;

/** One line of a tooltip: what the amount is and how much. */
export function lineText(line: ForecastLine): string {
  const detail = line.detail;
  const label = line.source === "baseline" ? `Baseline (${detail.charAt(0).toLowerCase()}${detail.slice(1)})`
    : line.source === "consulting" ? `Consulting, ${detail}`
      : line.source === "recurring" ? `${detail} (recurring)`
        : line.source === "planned" ? (detail ? `Planned: ${detail}` : "Planned item")
          : line.source === "reimbursement" ? `Reimbursement: ${detail}`
            : detail;
  return `${label}: ${tipMoney(Number(line.amount))}`;
}

/** The lines of a tooltip, one per row; a long list ends with a count of the rest. */
export function linesText(lines: ForecastLine[], limit = 12): string {
  const shown = lines.slice(0, limit).map(lineText);
  if (lines.length > limit) shown.push(`… and ${lines.length - limit} more`);
  return shown.join("\n");
}

/** The tooltip of a projected closing balance: the figure and its range from the backtest. */
export function balanceText(month: ForecastMonth): string {
  const range = `Projected ${tipMoney(Number(month.closing))} (${tipMoney(Number(month.low))} – ${tipMoney(Number(month.high))})`;
  return month.flags.includes("transfer_short") ? `${range}\n${TRANSFER_SHORT_TEXT}` : range;
}

/** The forecast's months of a year, indexed by month number minus one; undefined where it has none. */
export function monthsOfYear(forecast: Forecast | null, year: string): (ForecastMonth | undefined)[] {
  return Array.from({ length: 12 }, (_, index) =>
    forecast?.months.find((month) => month.month === `${year}-${String(index + 1).padStart(2, "0")}`));
}

/** Years the forecast reaches that the report has no data for. */
export function forecastOnlyYears(forecast: Forecast | null, reportYears: string[]): string[] {
  if (!forecast) return [];
  const years = new Set(forecast.months.map((month) => month.month.slice(0, 4)));
  return [...years].filter((year) => !reportYears.includes(year)).sort();
}

/** The sum of money amounts in euros, without floating-point drift. */
export const sumEuros = (values: number[]) => values.reduce((sum, value) => sum + Math.round(value * 100), 0) / 100;
