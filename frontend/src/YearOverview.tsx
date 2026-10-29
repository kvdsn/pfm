import React from "react";
import type { Budget, Category, CategorySummary, Forecast, ForecastLine, PartialSummary, SelectedCategory, Summary } from "./types";
import { budgetStatus } from "./budgets";
import { compactOverviewNumber, exactMoney, overviewNumber, sumMoney, wholeMoney } from "./format";
import { balanceText, linesText, monthsOfYear, projectedCategories, tipMoney } from "./forecast";

export function YearOverview({
  year,
  summaries,
  previousSummaries,
  previousYearToDate = null,
  emptyCategories,
  budgets = [],
  selectedCategory,
  onSelectCategory,
  onBalanceHistory,
  forecast = null,
  categories = [],
  today = new Date(),
}: {
  year: string;
  summaries: Summary[];
  /** Summaries of the year before, for the year-over-year column; empty hides the column. */
  previousSummaries: Summary[];
  /** Last year's month of this year's latest transaction, up to the same day, so a partial month compares fairly. */
  previousYearToDate?: PartialSummary | null;
  emptyCategories: Category[];
  /** The entity's budgets; those for another year are ignored. With none, the Left column is hidden. */
  budgets?: Budget[];
  selectedCategory: SelectedCategory | null;
  onSelectCategory: (category: SelectedCategory | null) => void;
  /** Opens the balance history graph. */
  onBalanceHistory: () => void;
  /** The entity's forecast: the rest of the current year, and next year, show projected amounts. Null shows none. */
  forecast?: Forecast | null;
  /** The entity's categories, to place projected lines in the income or expense group. */
  categories?: Category[];
  /** The current date, injectable for tests. */
  today?: Date;
}) {
  // Only the current year has a current and future months; past years render plainly.
  const currentMonthIndex = String(today.getFullYear()) === year ? today.getMonth() : null;
  // A year after the current one exists only as a forecast: all twelve months are projected.
  const forecastYear = forecast !== null && Number(year) > today.getFullYear();
  const isFuture = (index: number) => forecastYear || (currentMonthIndex !== null && index > currentMonthIndex);
  const monthClass = (index: number) =>
    index === currentMonthIndex ? "current-month" : isFuture(index) ? "future-month" : undefined;
  /**
   * A monthly cell; a future month shows its `projected` content, or stays empty so the column widths do not change.
   * `expected` marks a current-month cell whose content adds what is still expected to what is booked: it is italic too.
   */
  const monthCell = (index: number, content: React.ReactNode, className?: string, projected?: React.ReactNode, expected = false) => {
    const future = isFuture(index);
    const classes = [className, monthClass(index), (future && projected !== undefined) || expected ? "projected" : undefined]
      .filter(Boolean).join(" ") || undefined;
    return <td className={classes} key={index}>{future ? projected ?? null : content}</td>;
  };
  const monthForecasts = monthsOfYear(forecast, year);
  /** Per month, the projected lines per category; for the rest of the current year (its current month too), or all of a forecast year. */
  const projections = monthForecasts.map((month, index) =>
    month && (forecastYear || (currentMonthIndex !== null && index >= currentMonthIndex))
      ? projectedCategories(month, categories) : null);
  const hasProjection = !forecastYear && projections.some(Boolean);
  /** A projected amount; its tooltip explains it. */
  const projectedAmount = (value: number, note: string) =>
    value === 0 ? <span className="year-zero" title={note}>—</span> : <YearAmount value={value} note={note} />;
  /** The tooltip of a current-month cell that adds what is still expected to what is booked. */
  const expectedNote = (booked: number, expected: number, lines?: ForecastLine[]) =>
    `Booked ${tipMoney(booked)}, still expected ${tipMoney(expected)}${lines?.length ? `\n${linesText(lines)}` : ""}`;
  /** Whether a month is the current one and the forecast still expects something in it. */
  const stillExpected = (index: number, expected: number) =>
    index === currentMonthIndex && projections[index] !== null && Math.round(expected * 100) !== 0;
  const yearEndNote = (value: number) => `By year end, projected: ${tipMoney(value)}`;
  const monthlySummaries = Array.from({ length: 12 }, (_, index) => {
    const month = `${year}-${String(index + 1).padStart(2, "0")}`;
    return summaries.find((summary) => summary.month === month);
  });
  const monthLabels = Array.from({ length: 12 }, (_, index) =>
    new Date(Number(year), index, 1).toLocaleDateString(undefined, { month: "short" }),
  );
  const previousYear = String(Number(year) - 1);
  /** Months 1..comparedMonths of both years are compared: the year to date, or all twelve for a complete year. */
  const comparedMonths = monthlySummaries.reduce((latest, summary, index) => (summary ? index + 1 : latest), 0);
  // In the current year the last compared month is partial: compare it with last year's up to the same day.
  const cutOff = previousYearToDate
    && previousYearToDate.month === `${previousYear}-${String(comparedMonths).padStart(2, "0")}`
    ? previousYearToDate : null;
  const previousMonthly = Array.from({ length: 12 }, (_, index) => {
    if (cutOff && index === comparedMonths - 1) return cutOff;
    const month = `${previousYear}-${String(index + 1).padStart(2, "0")}`;
    return previousSummaries.find((summary) => summary.month === month);
  });
  const showDelta = previousSummaries.length > 0 && !forecastYear;
  const yearBudgets = new Map(budgets.filter((budget) => budget.year === year).map((budget) => [budget.category_id, budget]));
  const showLeft = yearBudgets.size > 0;
  /** A category's budget progress, or null without a budget. */
  const statusOf = (item: CategorySummary, flowType: "income" | "expense", used: number) => {
    const budget = item.category_id === null || forecastYear ? undefined : yearBudgets.get(item.category_id);
    return budget ? budgetStatus(budget, used, flowType === "income", year, today) : null;
  };
  /** The Left cell of a category row: what remains of its budget, or of its income target; `projectedEnd` is the year-end projection. */
  const leftCell = (status: ReturnType<typeof statusOf>, flowType: "income" | "expense", projectedEnd: number | null) => {
    if (!status) return <td><span className="year-zero">—</span></td>;
    const percent = Math.round(status.share * 100);
    const title = flowType === "income"
      ? `Received ${exactMoney(status.used)} of ${exactMoney(status.budget)} (${percent}%)`
      : `Used ${exactMoney(status.used)} of ${exactMoney(status.budget)} (${percent}%) · on pace: ${exactMoney(status.pace)} by now`;
    const missed = projectedEnd !== null && status.tone !== "over" && status.tone !== "met"
      && (flowType === "income" ? projectedEnd < status.budget : projectedEnd > status.budget);
    const projectedTitle = !missed ? "" : flowType === "income"
      ? `\nProjected ${tipMoney(projectedEnd)} by year end, ${tipMoney(status.budget - projectedEnd)} short of the target`
      : `\nProjected ${tipMoney(projectedEnd)} by year end, ${tipMoney(projectedEnd - status.budget)} over budget`;
    const tone = missed || status.tone === "over" ? "negative" : status.tone === "met" ? "positive" : status.tone === "ahead" ? "budget-ahead" : undefined;
    return (
      <td className={tone} title={title + projectedTitle}>
        {status.tone === "met" && "+"}
        <YearAmount value={status.tone === "met" ? -status.left : status.left} />
      </td>
    );
  };
  const flowTotals: Record<"income" | "expense", number[]> = {
    income: monthlySummaries.map((summary, index) => Number(forecastYear ? monthForecasts[index]?.income : summary?.income ?? 0)),
    expense: monthlySummaries.map((summary, index) => Number(forecastYear ? monthForecasts[index]?.expense : summary?.expenses ?? 0)),
  };
  /** What the forecast adds to a group's actual total by year end: the projected rest of each month from the current one on. */
  const projectedAddition = (flowType: "income" | "expense") => sumAmounts(monthForecasts.map((month, index) =>
    month && projections[index] ? Number(month[flowType]) - Number(month.actual[flowType]) : 0));
  const sumAmounts = (amounts: number[]) =>
    amounts.reduce((sum, amount) => sum + Math.round(amount * 100), 0) / 100;
  const annualTotal = (flowType: "income" | "expense") =>
    sumAmounts(flowTotals[flowType]);
  /** One row per category in the flow, with twelve monthly amounts and counts, largest first. */
  const categoryRows = (flowType: "income" | "expense") => {
    type Row = {
      key: string; item: CategorySummary; amounts: number[]; counts: number[]; previousAmounts: number[];
      /** The projected amounts and lines per month, for the current month and later. */
      added: number[]; addedLines: ForecastLine[][];
    };
    const rows = new Map<string, Row>();
    const rowFor = (item: CategorySummary) => {
      const key = `${item.category_id ?? item.name}\u0000${flowType}`;
      let row = rows.get(key);
      if (!row) {
        row = {
          key, item, amounts: Array(12).fill(0), counts: Array(12).fill(0), previousAmounts: Array(12).fill(0),
          added: Array(12).fill(0), addedLines: Array.from({ length: 12 }, () => []),
        };
        rows.set(key, row);
      }
      return row;
    };
    monthlySummaries.forEach((summary, monthIndex) => {
      for (const item of summary?.categories ?? []) {
        if (item.flow_type !== flowType) continue;
        const row = rowFor(item);
        row.amounts[monthIndex] = Number(item.amount);
        row.counts[monthIndex] = item.transaction_count;
      }
    });
    // A forecast year lists only the categories with projected lines, and has nothing to compare with.
    if (!forecastYear) {
      // Categories active only in the previous year still get a row, so their drop is visible.
      previousMonthly.forEach((summary, monthIndex) => {
        for (const item of summary?.categories ?? []) {
          if (item.flow_type === flowType) rowFor(item).previousAmounts[monthIndex] = Number(item.amount);
        }
      });
      // Categories without activity sit in the group their Income setting puts them in; an older server omits it.
      for (const category of emptyCategories) {
        if ((category.is_income ? "income" : "expense") !== flowType) continue;
        rowFor({ category_id: category.id, flow_type: flowType, name: category.name, amount: "0", transaction_count: 0 });
      }
    }
    // Projected lines join the category's row, or start one; in a forecast year they are the row's amounts.
    projections.forEach((byCategory, monthIndex) => {
      for (const entry of byCategory?.values() ?? []) {
        if (entry.flow !== flowType) continue;
        const row = rowFor({ category_id: entry.category_id, flow_type: flowType, name: entry.name, amount: "0", transaction_count: 0 });
        row.added[monthIndex] = entry.amount;
        row.addedLines[monthIndex] = entry.lines;
        if (forecastYear) row.amounts[monthIndex] = entry.amount;
      }
    });
    return [...rows.values()].sort((left, right) => sumAmounts(right.amounts) - sumAmounts(left.amounts));
  };
  const formatAmount = (amount: number, note?: string) => <YearAmount value={amount} note={note} />;
  // A group whose total is zero or negative (refunds outweigh spending) has no meaningful shares.
  const formatShare = (amount: number, total: number) =>
    total <= 0 ? "—" : new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(amount / total);
  const periodSum = (amounts: number[]) => sumAmounts(amounts.slice(0, comparedMonths));
  const periodLabel = (id: string) => {
    const last = monthLabels[Math.max(comparedMonths, 1) - 1];
    return cutOff
      ? `${monthLabels[0]} 1–${last} ${Number(cutOff.through.slice(8))} ${id}`
      : `${monthLabels[0]}–${last} ${id}`;
  };
  const previousFlowTotals: Record<"income" | "expense", number[]> = {
    income: previousMonthly.map((summary) => Number(summary?.income ?? 0)),
    expense: previousMonthly.map((summary) => Number(summary?.expenses ?? 0)),
  };
  /** Year-over-year change cell; for expenses a decrease is the good direction. */
  const deltaCell = (flowType: "income" | "expense", current: number, previous: number) => {
    const title = `${wholeMoney(previous)} in ${periodLabel(previousYear)} → ${wholeMoney(current)} in ${periodLabel(year)}`;
    if (previous === 0) {
      return <td title={title}>{current === 0 ? "—" : "new"}</td>;
    }
    const change = (current - previous) / Math.abs(previous);
    const better = flowType === "income" ? change > 0 : change < 0;
    const tone = Math.abs(change) < 0.01 ? "" : better ? "positive" : "negative";
    return (
      <td className={tone || undefined} title={title}>
        {deltaPercent.format(change).replace("-", "−")}
      </td>
    );
  };
  const toggleCategory = (item: CategorySummary) => {
    onSelectCategory(
      selectedCategory?.category_id === item.category_id && selectedCategory.flow_type === item.flow_type
        ? null
        : { category_id: item.category_id, flow_type: item.flow_type, name: item.name },
    );
  };
  const netAmounts = monthlySummaries.map((summary, index) => Number(forecastYear ? monthForecasts[index]?.net : summary?.net ?? 0));
  const balanceAmounts = monthlySummaries.map((summary, index) =>
    forecastYear
      ? (monthForecasts[index] ? Number(monthForecasts[index]!.closing) : null)
      : summary?.account_balances.length
        ? Number(sumMoney(summary.account_balances.map((balance) => balance.closing)))
        : null,
  );
  /** A row's year total: in the current year with a forecast, the projected year-end total; otherwise what is booked. */
  const yearTotal = (booked: number, projectedEnd: number) => (hasProjection ? projectedEnd : booked);
  /**
   * The Total cell: in the current year with a forecast, the projected year-end total in italics, with what is
   * booked so far in its tooltip; a forecast year's Total is projected too.
   */
  const totalCell = (booked: number, projectedEnd: number) => {
    const projected = forecastYear || (hasProjection && Math.round((projectedEnd - booked) * 100) !== 0);
    const note = hasProjection ? `Booked so far ${tipMoney(booked)}\n${yearEndNote(projectedEnd)}` : undefined;
    return <td className={projected ? "projected" : undefined}>{formatAmount(yearTotal(booked, projectedEnd), note)}</td>;
  };
  const latestBalance = [...balanceAmounts].reverse().find((amount) => amount !== null) ?? null;
  /** The projected balance at the end of the shown year, when the forecast reaches it. */
  const yearEndClosing = monthForecasts[11] ? Number(monthForecasts[11].closing) : null;
  const netEnd = sumAmounts(netAmounts) + projectedAddition("income") - projectedAddition("expense");
  const ownTransferAmounts = monthlySummaries.map((summary) => Number(summary?.own_transfers ?? 0));
  const ownTransfersTotal = sumAmounts(ownTransferAmounts);
  const ownTransfersActive = selectedCategory?.flow_type === "own_transfer";
  const toggleOwnTransfers = () => onSelectCategory(
    ownTransfersActive ? null : { category_id: null, flow_type: "own_transfer", name: "Between own accounts" },
  );

  return (
    <section className="year-overview">
      <div className="table-scroll year-table-scroll">
        <table className="year-summary-table">
          <thead>
            <tr>
              <th scope="col">Category</th>
              {monthLabels.map((label, index) => (
                <th scope="col" className={monthClass(index)} key={index}>{label}</th>
              ))}
              <th scope="col">Total</th>
              {showLeft && <th scope="col" title="What is left of the budget; for income, what is still to come">Left</th>}
              {showDelta && (
                <th scope="col" aria-label={`vs ${previousYear}`} title={`Change against the same period of ${previousYear}`}>
                  <span className="year-amount-full">vs {previousYear}</span>
                  <span className="year-amount-compact">’{previousYear.slice(2)}</span>
                </th>
              )}
              <th scope="col">Avg</th>
              <th scope="col">%</th>
            </tr>
          </thead>
          <tbody>
            {(["income", "expense"] as const).map((flowType) => {
              const rows = categoryRows(flowType);
              const total = annualTotal(flowType);
              const groupEnd = total + projectedAddition(flowType);
              const label = flowType === "income" ? "Income" : "Expenses";
              const lefts = rows.flatMap(({ item, amounts }) => {
                const status = statusOf(item, flowType, sumAmounts(amounts));
                return status ? [status.left] : [];
              });
              return (
                <React.Fragment key={flowType}>
                  <tr className={`year-group-total ${flowType}`}>
                    <th scope="row">{label} total</th>
                    {flowTotals[flowType].map((amount, index) => {
                      const month = monthForecasts[index];
                      const lines = [...(projections[index]?.values() ?? [])].filter((entry) => entry.flow === flowType).flatMap((entry) => entry.lines);
                      const expected = month && projections[index] ? Number(month[flowType]) - amount : 0;
                      const partial = stillExpected(index, expected);
                      return monthCell(
                        index,
                        partial ? projectedAmount(amount + expected, expectedNote(amount, expected, lines)) : formatAmount(amount),
                        undefined,
                        month && projectedAmount(Number(month[flowType]), `Projected ${flowType === "income" ? "income" : "expenses"} ${tipMoney(Number(month[flowType]))}\n${linesText(lines)}`),
                        partial,
                      );
                    })}
                    {totalCell(total, groupEnd)}
                    {showLeft && <td>{lefts.length ? formatAmount(sumAmounts(lefts)) : <span className="year-zero">—</span>}</td>}
                    {showDelta && deltaCell(flowType, periodSum(flowTotals[flowType]), periodSum(previousFlowTotals[flowType]))}
                    <td>{formatAmount(yearTotal(total, groupEnd) / 12)}</td>
                    <td>{yearTotal(total, groupEnd) <= 0 ? "—" : "100%"}</td>
                  </tr>
                  {rows.map(({ key, item, amounts, counts, previousAmounts, added, addedLines }) => {
                    const categoryTotal = sumAmounts(amounts);
                    const yearEnd = categoryTotal + sumAmounts(added);
                    const active = selectedCategory?.category_id === item.category_id
                      && selectedCategory.flow_type === item.flow_type;
                    return (
                      <tr
                        className={`year-category-row ${flowType}${active ? " active-filter-row" : ""}`}
                        key={key}
                        tabIndex={0}
                        aria-selected={active}
                        aria-label={`Filter transactions by ${item.name} ${flowType}`}
                        onClick={() => toggleCategory(item)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            toggleCategory(item);
                          } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                            const categoryRows = [
                              ...(event.currentTarget.closest("tbody")?.querySelectorAll<HTMLElement>(".year-category-row") ?? []),
                            ];
                            const index = categoryRows.indexOf(event.currentTarget);
                            categoryRows[index + (event.key === "ArrowDown" ? 1 : -1)]?.focus();
                            event.preventDefault();
                            event.stopPropagation();
                          }
                        }}
                      >
                        <th scope="row" title={item.name}>
                          <div className="category-cell">
                            <span className="category-name-static">{item.name}</span>
                            {!forecastYear && <span className="category-row-count">{counts.reduce((total, count) => total + count, 0)}</span>}
                          </div>
                        </th>
                        {amounts.map((amount, index) => {
                          const lines = addedLines[index];
                          // The current month adds what is still expected to what is booked, in italics.
                          const partial = stillExpected(index, added[index]);
                          return monthCell(
                            index,
                            partial
                              ? projectedAmount(amount + added[index], expectedNote(amount, added[index], lines))
                              : amount === 0 ? <span className="year-zero">—</span> : formatAmount(amount),
                            undefined,
                            projections[index] ? projectedAmount(added[index], linesText(lines)) : undefined,
                            partial,
                          );
                        })}
                        {totalCell(categoryTotal, yearEnd)}
                        {showLeft && leftCell(statusOf(item, flowType, categoryTotal), flowType, hasProjection ? yearEnd : null)}
                        {showDelta && deltaCell(flowType, periodSum(amounts), periodSum(previousAmounts))}
                        <td>{formatAmount(yearTotal(categoryTotal, yearEnd) / 12)}</td>
                        <td>{formatShare(yearTotal(categoryTotal, yearEnd), yearTotal(total, groupEnd))}</td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              );
            })}
            <tr className="year-metric-row net-change">
              <th scope="row">Net change</th>
              {netAmounts.map((amount, index) => {
                const month = monthForecasts[index];
                const projected = month && projections[index] ? Number(month.net) : null;
                const partial = projected !== null && stillExpected(index, projected - amount);
                return monthCell(
                  index,
                  partial ? projectedAmount(projected!, expectedNote(amount, projected! - amount)) : formatAmount(amount),
                  (partial || isFuture(index) ? projected ?? amount : amount) >= 0 ? "positive" : "negative",
                  projected === null ? undefined : projectedAmount(
                    projected,
                    `Projected income ${tipMoney(Number(month!.income))}, expenses ${tipMoney(Number(month!.expense))}`,
                  ),
                  partial,
                );
              })}
              {totalCell(sumAmounts(netAmounts), netEnd)}
              {showLeft && <td>—</td>}
              {showDelta && <td>—</td>}
              <td>{formatAmount(yearTotal(sumAmounts(netAmounts), netEnd) / 12)}</td>
              <td>—</td>
            </tr>
            <tr className="year-metric-row">
              <th scope="row">
                <span className="latest-balance-label">
                  Latest balance
                  <button
                    className="icon-button balance-history-icon"
                    aria-label="Show balance history"
                    title="Balance history"
                    onClick={onBalanceHistory}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M3 18.5h18M5.5 15l4-4 3 2.5 6-7" />
                      <path d="M15.5 6.5h3v3" />
                    </svg>
                  </button>
                </span>
              </th>
              {balanceAmounts.map((amount, index) => {
                const month = monthForecasts[index];
                const projected = month && projections[index] && (isFuture(index) || index === currentMonthIndex) ? month : null;
                // The current month shows the projected month end; the latest booked balance stays in the Total column.
                const partial = projected !== null && index === currentMonthIndex && Number(projected.closing) !== amount;
                return monthCell(
                  index,
                  partial
                    ? projectedAmount(Number(projected!.closing), `Latest booked ${amount === null ? "—" : tipMoney(amount)}\n${balanceText(projected!)}`)
                    : amount === null ? "—" : formatAmount(amount),
                  projected?.flags.includes("transfer_short") ? "transfer-short" : undefined,
                  projected ? projectedAmount(Number(projected.closing), balanceText(projected)) : undefined,
                  partial,
                );
              })}
              {hasProjection && yearEndClosing !== null ? (
                <td className="projected">
                  {formatAmount(yearEndClosing, `Latest booked ${latestBalance === null ? "—" : tipMoney(latestBalance)}\nProjected at year end ${tipMoney(yearEndClosing)}`)}
                </td>
              ) : <td>{latestBalance === null ? "—" : formatAmount(latestBalance)}</td>}
              {showLeft && <td>—</td>}
              {showDelta && <td>—</td>}
              <td>—</td>
              <td>—</td>
            </tr>
            {ownTransfersTotal > 0 && (
              <tr
                className={`year-own-transfers${ownTransfersActive ? " active-filter-row" : ""}`}
                tabIndex={0}
                aria-selected={ownTransfersActive}
                title="Moved between your own accounts; not counted as income or expenses"
                onClick={toggleOwnTransfers}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggleOwnTransfers();
                  }
                }}
              >
                <th scope="row">⇄ Between own accounts</th>
                {ownTransferAmounts.map((amount, index) => monthCell(index, amount === 0 ? "—" : formatAmount(amount)))}
                <td>{formatAmount(ownTransfersTotal)}</td>
                {showLeft && <td>—</td>}
                {showDelta && <td>—</td>}
                <td>—</td>
                <td>—</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const deltaPercent = new Intl.NumberFormat(undefined, {
  style: "percent",
  maximumFractionDigits: 0,
  signDisplay: "exceptZero",
});

export function YearAmount({ value, note }: { value: number | string; note?: string }) {
  const amount = Number(value);
  const formattedValue = `${amount < 0 ? "-" : ""}${overviewNumber.format(Math.abs(amount))}`;
  const magnitude = Math.abs(amount);
  const compactValue = magnitude >= 1_000_000
    ? `${amount < 0 ? "-" : ""}${compactOverviewNumber.format(magnitude / 1_000_000)}m`
    : magnitude >= 1_000
      ? `${amount < 0 ? "-" : ""}${compactOverviewNumber.format(magnitude / 1_000)}k`
      : magnitude >= 100
        ? `${amount < 0 ? "-" : ""}${Math.round(magnitude)}`
        : `${amount < 0 ? "-" : ""}${overviewNumber.format(magnitude)}`;
  return (
    <span aria-label={`${formattedValue} euro`} title={note ? `${formattedValue} euro\n${note}` : `${formattedValue} euro`}>
      <span className="year-amount-full">{formattedValue}</span>
      <span className="year-amount-compact">{compactValue}</span>
    </span>
  );
}
