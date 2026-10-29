import type { Budget } from "./types";

/** The share of the year that has passed: months so far (counting the current one) of twelve; 1 for past years, 0 for future ones. */
export const elapsedFraction = (year: string, today: Date) => {
  const current = today.getFullYear();
  if (Number(year) < current) return 1;
  if (Number(year) > current) return 0;
  return (today.getMonth() + 1) / 12;
};

export type BudgetStatus = {
  budget: number;
  used: number;
  /** Budget minus used. For an income target, what is still to come; negative once exceeded. */
  left: number;
  /** Used as a fraction of the budget. */
  share: number;
  /** What an even spread would have used by now. */
  pace: number;
  /**
   * An expense budget: "over" when used exceeds it, "ahead" when spending runs ahead of pace, else "ok".
   * An income target: "met" once reached, else "ok".
   */
  tone: "ok" | "ahead" | "over" | "met";
};

export function budgetStatus(budget: Budget, used: number, isIncome: boolean, year: string, today: Date): BudgetStatus {
  const amount = Number(budget.amount);
  const pace = amount * elapsedFraction(year, today);
  const left = Math.round((amount - used) * 100) / 100;
  let tone: BudgetStatus["tone"] = "ok";
  if (isIncome) {
    if (left < 0) tone = "met";
  } else if (left < 0) {
    tone = "over";
  } else if (used > pace) {
    tone = "ahead";
  }
  return { budget: amount, used, left, share: amount ? used / amount : 0, pace, tone };
}
