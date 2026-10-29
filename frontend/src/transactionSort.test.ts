import { describe, expect, it } from "vitest";
import { defaultSort, nextSort, sortTransactions } from "./transactionSort";
import type { Transaction } from "./types";

const row = (id: number, booking_date: string, amount: string) => ({ id, booking_date, amount }) as Transaction;
// As the server lists them: newest first, same-day rows in bank order.
const listed = [row(1, "2025-03-02", "-5.00"), row(2, "2025-03-02", "100.00"), row(3, "2025-03-01", "-40.00"), row(4, "2025-02-27", "-5.00")];
const ids = (rows: Transaction[]) => rows.map((item) => item.id);

describe("sortTransactions", () => {
  it("keeps the server order without a sort, and for newest first", () => {
    expect(sortTransactions(listed, null)).toBe(listed);
    expect(ids(sortTransactions(listed, { key: "date", direction: "desc" }))).toEqual([1, 2, 3, 4]);
  });

  it("mirrors newest first exactly for oldest first", () => {
    expect(ids(sortTransactions(listed, { key: "date", direction: "asc" }))).toEqual([4, 3, 2, 1]);
  });

  it("sorts signed amounts, ties in date order", () => {
    expect(ids(sortTransactions(listed, { key: "amount", direction: "desc" }))).toEqual([2, 1, 4, 3]);
    expect(ids(sortTransactions(listed, { key: "amount", direction: "asc" }))).toEqual([3, 4, 1, 2]);
  });
});

describe("nextSort and defaultSort", () => {
  it("flips the sorted column and starts another one descending", () => {
    expect(nextSort({ key: "date", direction: "desc" }, "date")).toEqual({ key: "date", direction: "asc" });
    expect(nextSort({ key: "date", direction: "asc" }, "amount")).toEqual({ key: "amount", direction: "desc" });
    expect(nextSort(null, "date")).toEqual({ key: "date", direction: "desc" });
  });

  it("shows the server order of each follow-up filter", () => {
    expect(defaultSort("all")).toEqual({ key: "date", direction: "desc" });
    expect(defaultSort("open")).toEqual({ key: "date", direction: "asc" });
    expect(defaultSort("done")).toBeNull();
  });
});
