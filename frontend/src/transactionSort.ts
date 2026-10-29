import type { FollowUpFilter, Transaction } from "./types";

export type SortKey = "date" | "amount";
export type SortDirection = "asc" | "desc";
export interface TransactionSort {
  key: SortKey;
  direction: SortDirection;
}

/** The order the server lists in: newest first, open follow-ups oldest first, done ones by when they were done (no column). */
export function defaultSort(filter: FollowUpFilter): TransactionSort | null {
  if (filter === "all") return { key: "date", direction: "desc" };
  if (filter === "open") return { key: "date", direction: "asc" };
  return null;
}

/** A header click: the sorted column flips direction; another column starts newest or highest first. */
export function nextSort(current: TransactionSort | null, key: SortKey): TransactionSort {
  if (current?.key === key) return { key, direction: current.direction === "desc" ? "asc" : "desc" };
  return { key, direction: "desc" };
}

/**
 * The transactions in the chosen order; amounts sort signed, so highest first puts income on top and lowest first
 * the largest expenses. Ties keep the server's order, reversed when ascending, so newest first and oldest first
 * mirror each other exactly. No sort keeps the server's order.
 */
export function sortTransactions(transactions: Transaction[], sort: TransactionSort | null): Transaction[] {
  if (!sort) return transactions;
  const sign = sort.direction === "asc" ? 1 : -1;
  const value = (transaction: Transaction) => (sort.key === "date" ? transaction.booking_date : Number(transaction.amount));
  return transactions
    .map((transaction, index) => ({ transaction, index }))
    .sort((a, b) => {
      const left = value(a.transaction);
      const right = value(b.transaction);
      if (left !== right) return (left < right ? -1 : 1) * sign;
      return (a.index - b.index) * -sign;
    })
    .map(({ transaction }) => transaction);
}
