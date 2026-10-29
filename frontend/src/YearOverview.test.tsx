import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { YearOverview } from "./YearOverview";
import type { Budget, Category, CategorySummary, Forecast, ForecastLine, ForecastMonth, PartialSummary, Summary } from "./types";

afterEach(cleanup);

const item = (name: string, flow_type: "income" | "expense", amount: number, id: number): CategorySummary => ({
  category_id: id,
  flow_type,
  name,
  amount: amount.toFixed(2),
  transaction_count: 1,
});

const summary = (month: string, categories: CategorySummary[]): Summary => {
  const total = (flow: string) => categories.filter((c) => c.flow_type === flow).reduce((sum, c) => sum + Number(c.amount), 0);
  return {
    month,
    income: total("income").toFixed(2),
    expenses: total("expense").toFixed(2),
    net: (total("income") - total("expense")).toFixed(2),
    own_transfers: "0.00",
    categories,
    account_balances: [],
    transaction_count: categories.length,
  };
};

const renderOverview = (
  summaries: Summary[],
  previousSummaries: Summary[],
  today = new Date(2026, 5, 15),
  year = "2026",
  budgets: Budget[] = [],
  emptyCategories: Category[] = [],
  previousYearToDate: PartialSummary | null = null,
) =>
  render(
    <YearOverview
      today={today}
      year={year}
      summaries={summaries}
      previousSummaries={previousSummaries}
      previousYearToDate={previousYearToDate}
      emptyCategories={emptyCategories}
      budgets={budgets}
      selectedCategory={null}
      onSelectCategory={vi.fn()}
      onBalanceHistory={vi.fn()}
    />,
  );

const rowOf = (name: string) => screen.getByRole("row", { name: new RegExp(`Filter transactions by ${name} `) });
/** The year-over-year cell: the one right after Total, found by its tooltip. */
const deltaCell = (row: HTMLElement) => within(row).getAllByRole("cell").find((cell) => cell.title.includes("→"))!;
const minus = "−";

// Year to date: January and February of 2026.
const current = [
  summary("2026-01", [item("Salary", "income", 1200, 1), item("Rent", "expense", 600, 2), item("Fun", "expense", 100, 3)]),
  summary("2026-02", [item("Salary", "income", 1200, 1), item("Rent", "expense", 600, 2), item("Bonus", "income", 50, 5)]),
];
const previous = [
  summary("2025-01", [item("Salary", "income", 1000, 1), item("Rent", "expense", 800, 2), item("Gym", "expense", 40, 4)]),
  summary("2025-02", [item("Salary", "income", 1000, 1), item("Rent", "expense", 800, 2)]),
  // After the compared period, so it must not count.
  summary("2025-06", [item("Salary", "income", 5000, 1), item("Rent", "expense", 5000, 2)]),
];

describe("YearOverview year-over-year column", () => {
  it("is hidden without previous data", () => {
    renderOverview(current, []);
    expect(screen.queryByText(/^vs /)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/→/)).not.toBeInTheDocument();
  });

  it("shows the previous year in the header", () => {
    renderOverview(current, previous);
    expect(screen.getByRole("columnheader", { name: "vs 2025" })).toBeInTheDocument();
  });

  it("shows signed percents, good for more income and less spending", () => {
    renderOverview(current, previous);
    const salary = deltaCell(rowOf("Salary"));
    expect(salary).toHaveTextContent("+20%");
    expect(salary).toHaveClass("positive");
    const rent = deltaCell(rowOf("Rent"));
    expect(rent).toHaveTextContent(`${minus}25%`);
    expect(rent).toHaveClass("positive");
  });

  it("marks less income and more spending as bad", () => {
    renderOverview(
      [summary("2026-01", [item("Salary", "income", 800, 1), item("Rent", "expense", 900, 2)])],
      [summary("2025-01", [item("Salary", "income", 1000, 1), item("Rent", "expense", 600, 2)])],
    );
    const salary = deltaCell(rowOf("Salary"));
    expect(salary).toHaveTextContent(`${minus}20%`);
    expect(salary).toHaveClass("negative");
    const rent = deltaCell(rowOf("Rent"));
    expect(rent).toHaveTextContent("+50%");
    expect(rent).toHaveClass("negative");
  });

  it("leaves changes under one percent uncolored", () => {
    renderOverview(
      [summary("2026-01", [item("Salary", "income", 1004, 1)])],
      [summary("2025-01", [item("Salary", "income", 1000, 1)])],
    );
    const salary = deltaCell(rowOf("Salary"));
    expect(salary).toHaveTextContent("0%");
    expect(salary).not.toHaveClass("positive");
    expect(salary).not.toHaveClass("negative");
  });

  it("shows new for categories without previous activity and a dash for none at all", () => {
    renderOverview(
      [summary("2026-01", [item("Bonus", "income", 50, 5)])],
      [summary("2025-01", [item("Salary", "income", 1000, 1)])],
    );
    const bonus = deltaCell(rowOf("Bonus"));
    expect(bonus).toHaveTextContent("new");
    expect(bonus).not.toHaveClass("positive");
    const zero = renderOverview(
      [summary("2026-01", [item("Zero", "expense", 0, 9)])],
      [summary("2025-01", [item("Salary", "income", 1000, 1)])],
    );
    expect(deltaCell(within(zero.container).getByRole("row", { name: /by Zero expense/ }))).toHaveTextContent("—");
  });

  it("compares a partial last month with last year's up to the same day", () => {
    const februaryTo14 = {
      ...summary("2025-02", [item("Salary", "income", 1000, 1), item("Rent", "expense", 400, 2)]),
      through: "2025-02-14",
    };
    renderOverview(current, previous, new Date(2026, 1, 14), "2026", [], [], februaryTo14);
    const rent = deltaCell(rowOf("Rent"));
    expect(rent).toHaveTextContent("0%");
    expect(rent.title).toMatch(/in Jan 1–Feb 14 2025 → .* in Jan 1–Feb 14 2026$/);
    expect(deltaCell(rowOf("Salary"))).toHaveTextContent("+20%");
  });

  it("ignores a cut-off month that is not the last compared month", () => {
    const march = { ...summary("2025-03", [item("Rent", "expense", 1, 2)]), through: "2025-03-05" };
    renderOverview(current, previous, new Date(2026, 5, 15), "2026", [], [], march);
    const rent = deltaCell(rowOf("Rent"));
    expect(rent).toHaveTextContent(`${minus}25%`);
    expect(rent.title).toContain("in Jan–Feb 2025");
  });

  it("keeps categories that only had previous activity, at minus 100 percent", () => {
    renderOverview(current, previous);
    const gym = deltaCell(rowOf("Gym"));
    expect(gym).toHaveTextContent(`${minus}100%`);
    expect(gym).toHaveClass("positive");
  });

  it("compares only the months up to the latest one with data", () => {
    renderOverview(current, previous);
    // Rent: 1,200 this year to date against 1,600 in Jan-Feb 2025; the 5,000 in June 2025 is ignored.
    expect(deltaCell(rowOf("Rent"))).toHaveTextContent(`${minus}25%`);
    expect(deltaCell(screen.getByRole("row", { name: /Expenses total/ }))).toHaveTextContent(`${minus}21%`);
  });

  it("explains each change in a tooltip", () => {
    renderOverview(current, previous);
    expect(deltaCell(rowOf("Salary")).title).toBe("€ 2.000 in Jan–Feb 2025 → € 2.400 in Jan–Feb 2026");
  });

  it("leaves the other rows without a change", () => {
    renderOverview(current, previous);
    const net = screen.getByRole("row", { name: /Net change/ });
    expect(within(net).getAllByRole("cell")[13]).toHaveTextContent("—");
  });
});

describe("YearOverview current and future months", () => {
  // Fixed "today" in June 2026: index 5 is the current month, 6..11 are future.
  const june = new Date(2026, 5, 15);
  const cellsOf = (row: HTMLElement) => within(row).getAllByRole("cell");

  it("tints the current month in the header and every row", () => {
    renderOverview(current, previous, june);
    const headers = screen.getAllByRole("columnheader");
    // Index 0 is the Category column, so month i sits at i + 1.
    expect(headers[6]).toHaveClass("current-month");
    expect(headers.filter((header) => header.classList.contains("current-month"))).toHaveLength(1);
    for (const row of screen.getAllByRole("row").slice(1)) {
      const marked = cellsOf(row).filter((cell) => cell.classList.contains("current-month"));
      if (row.className.includes("year-own-transfers")) continue;
      expect(marked).toHaveLength(1);
      expect(cellsOf(row).indexOf(marked[0])).toBe(5);
    }
  });

  it("dims future month headers and leaves their cells empty, including totals and net change", () => {
    renderOverview(current, previous, june);
    const headers = screen.getAllByRole("columnheader");
    for (let month = 6; month < 12; month += 1) expect(headers[month + 1]).toHaveClass("future-month");
    expect(headers[5]).not.toHaveClass("future-month");
    for (const name of [/Income total/, /Expenses total/, /Net change/, /Latest balance/, /Salary/]) {
      const cells = cellsOf(screen.getByRole("row", { name }));
      for (let month = 6; month < 12; month += 1) {
        expect(cells[month]).toHaveClass("future-month");
        expect(cells[month]).toBeEmptyDOMElement();
      }
    }
    // Past months keep their zeros, and the total column is unchanged.
    const expenses = cellsOf(screen.getByRole("row", { name: /Expenses total/ }));
    expect(expenses[3]).toHaveTextContent("0");
    expect(expenses[12]).toHaveTextContent("1.300");
  });

  it("shows no current or future styling for a past year", () => {
    const { container } = renderOverview(current, previous, new Date(2027, 5, 15));
    expect(container.querySelector(".current-month, .future-month")).toBeNull();
    const net = cellsOf(screen.getByRole("row", { name: /Net change/ }));
    expect(net[11]).toHaveTextContent("0");
  });
});

describe("YearOverview income categories", () => {
  const renderWithEmpty = (summaries: Summary[], emptyCategories: Category[], previousSummaries: Summary[] = []) =>
    render(
      <YearOverview
        today={new Date(2026, 5, 15)}
        year="2026"
        summaries={summaries}
        previousSummaries={previousSummaries}
        emptyCategories={emptyCategories}
        selectedCategory={null}
        onSelectCategory={vi.fn()}
        onBalanceHistory={vi.fn()}
      />,
    );

  it("groups categories without activity by is_income, treating a missing flag as expense", () => {
    renderWithEmpty([summary("2026-01", [item("Rent", "expense", 600, 2)])], [
      { id: 10, name: "Interest", rule_flow_type: "expense", is_income: true },
      { id: 11, name: "Gifts", rule_flow_type: "income", is_income: false },
      { id: 12, name: "Old server", rule_flow_type: "income" },
    ]);
    expect(screen.getByRole("row", { name: "Filter transactions by Interest income" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: "Filter transactions by Gifts expense" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: "Filter transactions by Old server expense" })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: "Filter transactions by Gifts income" })).not.toBeInTheDocument();
  });

  it("shows a negative expense total as a negative amount with a year-over-year change", () => {
    renderWithEmpty(
      [summary("2026-01", [item("Rent", "expense", 600, 2), item("Returns", "expense", -50, 3)])],
      [],
      [summary("2025-01", [item("Rent", "expense", 600, 2), item("Returns", "expense", -100, 3)])],
    );
    const returns = rowOf("Returns");
    expect(within(returns).getAllByLabelText("-50 euro").length).toBeGreaterThan(0);
    // Total 550: the category's share is -50 / 550.
    expect(within(returns).getByText(/^[-\u2212]9[.,]1%$/)).toBeInTheDocument();
    // -50 against -100 is a rise in expense, so not the good direction.
    expect(deltaCell(returns)).toHaveTextContent("+50%");
    expect(deltaCell(returns)).toHaveClass("negative");
  });

  it("shows no shares when a group total is not positive", () => {
    renderWithEmpty([summary("2026-01", [item("Returns", "expense", -50, 3)])], []);
    const row = rowOf("Returns");
    const cells = within(row).getAllByRole("cell");
    expect(cells[cells.length - 1]).toHaveTextContent("—");
    expect(within(screen.getByRole("row", { name: /Expenses total/ })).getAllByRole("cell").at(-1)).toHaveTextContent("—");
  });
});

describe("YearOverview Left column", () => {
  const budget = (category_id: number, amount: number, year = "2026"): Budget => ({
    category_id,
    year,
    amount: amount.toFixed(2),
  });
  // Through June (6 of 12 months): Travel 700 used, Fun 300, Rent 1300, Gym 50, Salary 3000, Bonus 200.
  const months = [
    summary("2026-01", [
      item("Travel", "expense", 700, 10),
      item("Fun", "expense", 300, 11),
      item("Rent", "expense", 1300, 12),
      item("Gym", "expense", 50, 13),
      item("Salary", "income", 3000, 1),
      item("Bonus", "income", 200, 5),
    ]),
  ];
  const today = new Date(2026, 5, 15);
  const leftOf = (name: string) => {
    const cells = within(rowOf(name)).getAllByRole("cell");
    // Twelve months, then Total, then Left.
    return cells[13];
  };
  /** A Left cell's text in the wide format, such as "+50" or "-200". */
  const text = (cell: HTMLElement) =>
    `${cell.textContent?.startsWith("+") ? "+" : ""}${cell.querySelector(".year-amount-full")?.textContent ?? cell.textContent}`;

  it("is absent without budgets, or when they are for another year", () => {
    renderOverview(months, [], today);
    expect(screen.queryByRole("columnheader", { name: "Left" })).not.toBeInTheDocument();
    cleanup();
    renderOverview(months, [], today, "2026", [budget(10, 2000, "2025")]);
    expect(screen.queryByRole("columnheader", { name: "Left" })).not.toBeInTheDocument();
  });

  it("sits right after Total", () => {
    renderOverview(months, [], today, "2026", [budget(10, 2000)]);
    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers.indexOf("Left")).toBe(headers.indexOf("Total") + 1);
  });

  it("shows what is left in the normal ink while spending is on pace", () => {
    renderOverview(months, [], today, "2026", [budget(10, 2000)]);
    const cell = leftOf("Travel");
    expect(text(cell)).toBe("1.300");
    expect(cell).not.toHaveClass("negative");
    expect(cell).not.toHaveClass("budget-ahead");
    expect(cell).toHaveAttribute("title", "Used € 700 of € 2.000 (35%) · on pace: € 1.000 by now");
  });

  it("turns amber when spending runs ahead of pace", () => {
    renderOverview(months, [], today, "2026", [budget(10, 1200)]);
    const cell = leftOf("Travel");
    expect(cell).toHaveClass("budget-ahead");
    expect(text(cell)).toBe("500");
    expect(cell).toHaveAttribute("title", "Used € 700 of € 1.200 (58%) · on pace: € 600 by now");
  });

  it("is on pace right at the pace line", () => {
    renderOverview(months, [], today, "2026", [budget(10, 1400)]);
    expect(leftOf("Travel")).not.toHaveClass("budget-ahead");
  });

  it("turns red with a minus when over budget", () => {
    renderOverview(months, [], today, "2026", [budget(10, 500)]);
    const cell = leftOf("Travel");
    expect(cell).toHaveClass("negative");
    expect(text(cell)).toBe("-200");
    expect(cell).toHaveAttribute("title", "Used € 700 of € 500 (140%) · on pace: € 250 by now");
  });

  it("counts a past year as fully elapsed", () => {
    renderOverview(
      [summary("2025-03", [item("Travel", "expense", 700, 10)])], [], today, "2025", [budget(10, 1000, "2025")],
    );
    const cell = leftOf("Travel");
    expect(text(cell)).toBe("300");
    expect(cell).not.toHaveClass("budget-ahead");
    expect(cell).toHaveAttribute("title", "Used € 700 of € 1.000 (70%) · on pace: € 1.000 by now");
  });

  it("shows the amount still to come for an income target, and green plus when exceeded", () => {
    renderOverview(months, [], today, "2026", [budget(1, 4000), budget(5, 150)]);
    const salary = leftOf("Salary");
    expect(text(salary)).toBe("1.000");
    expect(salary).not.toHaveClass("positive");
    expect(salary).toHaveAttribute("title", "Received € 3.000 of € 4.000 (75%)");
    const bonus = leftOf("Bonus");
    expect(text(bonus)).toBe("+50");
    expect(bonus).toHaveClass("positive");
  });

  it("shows a dash for categories without a budget, including empty ones with a budget", () => {
    renderOverview(months, [], today, "2026", [budget(10, 2000), budget(20, 300)], [
      { id: 20, name: "Pets", rule_flow_type: null },
    ]);
    expect(leftOf("Fun")).toHaveTextContent("—");
    expect(text(leftOf("Pets"))).toBe("300");
  });

  it("sums the lefts of budgeted categories in each group total", () => {
    renderOverview(months, [], today, "2026", [budget(10, 2000), budget(11, 250), budget(1, 4000)]);
    const expenses = screen.getByRole("row", { name: /^Expenses total/ });
    // 1300 left of Travel and 50 over for Fun.
    expect(text(within(expenses).getAllByRole("cell")[13])).toBe("1.250");
    const income = screen.getByRole("row", { name: /^Income total/ });
    expect(text(within(income).getAllByRole("cell")[13])).toBe("1.000");
  });

  it("shows a dash in a group total without budgeted categories", () => {
    renderOverview(months, [], today, "2026", [budget(10, 2000)]);
    const income = screen.getByRole("row", { name: /^Income total/ });
    expect(within(income).getAllByRole("cell")[13]).toHaveTextContent("—");
  });
});

describe("YearOverview forecast", () => {
  const categories: Category[] = [
    { id: 1, name: "Salary", rule_flow_type: null, is_income: true },
    { id: 2, name: "Rent", rule_flow_type: null },
    { id: 3, name: "Fun", rule_flow_type: null },
    { id: 9, name: "Uncategorized", rule_flow_type: null },
  ];
  const today = new Date(2026, 5, 15);
  const line = (category_id: number, category: string, amount: number, source: ForecastLine["source"], detail: string): ForecastLine => ({
    category_id, category, amount: amount.toFixed(2), source, date: null, detail,
  });
  const month = (key: string, lines: ForecastLine[], extra: Partial<ForecastMonth> = {}): ForecastMonth => {
    const isIncome = (l: ForecastLine) => l.category === "Salary" || (l.category === "Uncategorized" && Number(l.amount) > 0);
    const income = lines.filter(isIncome).reduce((sum, l) => sum + Number(l.amount), 0);
    const expense = lines.filter((l) => !isIncome(l)).reduce((sum, l) => sum - Number(l.amount), 0);
    return {
      month: key, partial: false, lines, actual: { income: "0.00", expense: "0.00" },
      income: income.toFixed(2), expense: expense.toFixed(2), net: (income - expense).toFixed(2),
      closing: "5000.00", low: "4500.00", high: "5600.00", flags: [], ...extra,
    };
  };
  const julyLines = [
    line(1, "Salary", 1200, "salary", "Monthly salary"),
    line(2, "Rent", -600, "recurring", "Landlord"),
    line(3, "Fun", 30, "baseline", "Median of the last 6 months"),
    line(9, "Uncategorized", -50, "baseline", "Median of the last 6 months"),
    line(9, "Uncategorized", 20, "planned", "Gift"),
  ];
  const forecast: Forecast = {
    account_type: "personal", today: "2026-06-15", accounts: [], opening: "5000.00",
    months: [
      month("2026-06", [line(2, "Rent", -100, "baseline", "Median of the last 6 months")], { partial: true, actual: { income: "0.00", expense: "600.00" } }),
      month("2026-07", julyLines, { closing: "30000.00", low: "29500.00", high: "31800.00" }),
      month("2026-08", [line(2, "Rent", -600, "recurring", "Landlord")], { flags: ["transfer_short"] }),
      month("2027-01", [line(1, "Salary", 1300, "salary", "Monthly salary"), line(2, "Rent", -650, "recurring", "Landlord")]),
    ],
    year_ends: [], one_offs: [], assumptions: [],
    backtest: { origins: 0, horizons: [], baseline: { baseline_months: 6, baseline_weighted: false }, recommended: { baseline_months: 6, baseline_weighted: false } },
  };
  const renderForecast = (options: { year?: string; budgets?: Budget[]; forecast?: Forecast | null; previous?: Summary[] } = {}) =>
    render(
      <YearOverview
        today={today}
        year={options.year ?? "2026"}
        summaries={options.year === "2027" ? [] : [...current, summary("2026-06", [item("Rent", "expense", 600, 2)])]}
        previousSummaries={options.previous ?? previous}
        emptyCategories={categories.filter((c) => c.name === "Fun")}
        budgets={options.budgets ?? []}
        forecast={options.forecast === undefined ? forecast : options.forecast}
        categories={categories}
        selectedCategory={null}
        onSelectCategory={vi.fn()}
        onBalanceHistory={vi.fn()}
      />,
    );
  /** A month's cell in a row: months start at the first cell. */
  const cellOf = (row: HTMLElement, monthIndex: number) => within(row).getAllByRole("cell")[monthIndex];
  const fullText = (cell: HTMLElement) => cell.querySelector(".year-amount-full")?.textContent ?? cell.textContent;
  const titleOf = (cell: HTMLElement) => cell.querySelector("[title]")?.getAttribute("title") ?? "";

  it("changes nothing without a forecast", () => {
    const { container } = renderForecast({ forecast: null });
    expect(container.querySelector(".projected")).toBeNull();
    expect(cellOf(rowOf("Rent"), 6)).toBeEmptyDOMElement();
  });

  it("projects future months in italics, with the rows' sign convention", () => {
    renderForecast();
    const july = (name: string) => cellOf(rowOf(name), 6);
    expect(july("Rent")).toHaveClass("projected");
    expect(fullText(july("Rent"))).toBe("600");
    expect(fullText(july("Salary"))).toBe("1.200");
    // A refund lowers an expense.
    expect(fullText(july("Fun"))).toBe("-30");
    // Uncategorized splits by sign, like the report.
    const uncategorized = (flow: string) => cellOf(screen.getByRole("row", { name: `Filter transactions by Uncategorized ${flow}` }), 6);
    expect(fullText(uncategorized("expense"))).toBe("50");
    expect(fullText(uncategorized("income"))).toBe("20");
  });

  it("lists the lines in a projected cell's tooltip", () => {
    renderForecast();
    const rent = titleOf(cellOf(rowOf("Rent"), 6));
    expect(rent).toContain("Landlord (recurring): −€600");
    const fun = titleOf(cellOf(rowOf("Fun"), 6));
    expect(fun).toContain("Baseline (median of the last 6 months): €30");
  });

  it("projects the group rows, the net change and the balance", () => {
    renderForecast();
    const incomeTotal = screen.getByRole("row", { name: /Income total/ });
    expect(fullText(cellOf(incomeTotal, 6))).toBe("1.220");
    const expenseTotal = screen.getByRole("row", { name: /Expenses total/ });
    expect(fullText(cellOf(expenseTotal, 6))).toBe("620");
    const balance = screen.getByRole("row", { name: /Latest balance/ });
    expect(fullText(cellOf(balance, 6))).toBe("30.000");
    expect(titleOf(cellOf(balance, 6))).toContain("Projected €30.000 (€29.500 – €31.800)");
    expect(cellOf(balance, 7)).toHaveClass("transfer-short");
    expect(titleOf(cellOf(balance, 7))).toContain("Transfer short");
  });

  it("adds what the current month still expects to what is booked, in italics", () => {
    renderForecast();
    const rent = cellOf(rowOf("Rent"), 5);
    expect(rent).toHaveClass("projected", "current-month");
    expect(fullText(rent)).toBe("700");
    expect(titleOf(rent)).toContain("Booked €600, still expected €100");
    // The Total is the projected year-end total: 1.800 booked, plus 100 still expected in June and 600 in July and August.
    const cells = within(rowOf("Rent")).getAllByRole("cell");
    expect(cells[12]).toHaveClass("projected");
    expect(fullText(cells[12])).toBe("3.100");
    expect(titleOf(cells[12])).toContain("Booked so far €1.800");
    expect(titleOf(cells[12])).toContain("By year end, projected: €3.100");
    // Avg follows the projected total.
    expect(fullText(cells[cells.length - 2])).toBe("258");
  });

  it("keeps a current-month cell with nothing left to expect as booked", () => {
    const quiet = { ...forecast, months: forecast.months.map((m) => (m.month === "2026-06" ? { ...m, lines: [], expense: "600.00" } : m)) };
    renderForecast({ forecast: quiet });
    const rent = cellOf(rowOf("Rent"), 5);
    expect(rent).not.toHaveClass("projected");
    expect(fullText(rent)).toBe("600");
  });

  it("adds the year-end projection to the Total's tooltip, and an over-budget Left cell", () => {
    // Rent: 1200 + 600 booked (Jan, Feb, Jun) ... the actual total plus the projected months.
    renderForecast({ budgets: [{ category_id: 2, year: "2026", amount: "2000.00" }] });
    const cells = within(rowOf("Rent")).getAllByRole("cell");
    // Actual 600 + 600 + 600 = 1800; projected 100 (June rest) + 600 (July) + 600 (August) = 1300.
    expect(titleOf(cells[12])).toContain("By year end, projected: €3.100");
    expect(cells[13]).toHaveClass("negative");
    expect(cells[13].getAttribute("title")).toContain("Projected €3.100 by year end, €1.100 over budget");
  });

  it("shows next year as a forecast: all months projected, no year-over-year, only categories with lines", () => {
    const { container } = renderForecast({ year: "2027" });
    expect(screen.queryByRole("columnheader", { name: /^vs / })).not.toBeInTheDocument();
    expect(fullText(cellOf(rowOf("Salary"), 0))).toBe("1.300");
    expect(cellOf(rowOf("Salary"), 0)).toHaveClass("projected");
    expect(screen.queryByRole("row", { name: /by Fun / })).not.toBeInTheDocument();
    // The Total is the projected total, in italics.
    const total = within(rowOf("Rent")).getAllByRole("cell")[12];
    expect(total).toHaveClass("projected");
    expect(fullText(total)).toBe("650");
    expect(container.querySelectorAll("thead th.future-month")).toHaveLength(12);
  });
});
