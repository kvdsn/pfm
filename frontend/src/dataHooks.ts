import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AccountType,
  BalanceHistory,
  Budget,
  Category,
  FollowUpFilter,
  Forecast,
  ForecastVsActual,
  ImportRecord,
  PartialSummary,
  SelectedCategory,
  Summary,
  Transaction,
} from "./types";
import { api, errorMessage } from "./api";
import { forecastOnlyYears } from "./forecast";

const TRANSACTION_PAGE_SIZE = 1000;

/** Distinct years of the given YYYY-MM months, oldest first. */
export const yearsOf = (months: string[]) => [...new Set(months.map((month) => month.slice(0, 4)))].sort();

/** An entity's forecast; null when the server has none (an older server answers with something else). */
const fetchForecast = (accountType: AccountType) =>
  api<Forecast>(`/api/forecast?account_type=${accountType}`)
    .then((forecast) => (Array.isArray(forecast?.months) ? forecast : null), () => null);

/** The forecast-versus-actual comparison of Personal or Business; null when the server has none (an older server). */
const fetchForecastVsActual = (accountType: AccountType) =>
  api<ForecastVsActual>(`/api/forecast/vs-actual?account_type=${accountType}`)
    .then((vs) => (Array.isArray(vs?.months) && vs.summary ? vs : null), () => null);

/**
 * The report, categories, imports and forecast of the active entity, loaded together by `refresh`.
 * `refresh` returns the report, or null when it failed or a newer request replaced it. After a load the
 * selected year is kept if it is still available, otherwise it moves to the latest year with data.
 */
export function useReport(
  accountType: AccountType,
  setYear: (update: (current: string) => string) => void,
  setError: (message: string) => void,
) {
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [previousYearToDate, setPreviousYearToDate] = useState<PartialSummary | null>(null);
  const [months, setMonths] = useState<string[]>([]);
  /** The forecast of one entity; it only counts while that entity is the active one. */
  const [loadedForecast, setLoadedForecast] = useState<Forecast | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [imports, setImports] = useState<ImportRecord[]>([]);
  const refreshRequest = useRef(0);

  const refresh = useCallback(async () => {
    const request = ++refreshRequest.current;
    setError("");
    try {
      const [report, nextCategories, nextImports, nextForecast] = await Promise.all([
        api<{ months: string[]; summaries: Summary[]; previous_year_to_date?: PartialSummary | null }>(
          `/api/report?account_type=${accountType}`,
        ),
        api<Category[]>(`/api/categories?account_type=${accountType}`),
        api<ImportRecord[]>(`/api/imports?account_type=${accountType}`),
        fetchForecast(accountType),
      ]);
      if (request !== refreshRequest.current) return null;
      setLoadedForecast(nextForecast);
      setMonths(report.months);
      setSummaries(report.summaries);
      setPreviousYearToDate(report.previous_year_to_date ?? null);
      setCategories(nextCategories);
      setImports(nextImports);
      const reported = yearsOf(report.months);
      const available = [...reported, ...forecastOnlyYears(nextForecast, reported)];
      setYear((current) => (available.includes(current) ? current : reported.at(-1) || ""));
      return report;
    } catch (cause) {
      if (request !== refreshRequest.current) return null;
      setError(errorMessage(cause, "Could not load reports"));
      return null;
    }
  }, [accountType]);

  /** Reload the forecast after a change in the Plan or Recurring view. */
  const reloadForecast = useCallback(async () => {
    setLoadedForecast(await fetchForecast(accountType));
  }, [accountType]);

  return { summaries, previousYearToDate, months, loadedForecast, categories, imports, refresh, reloadForecast };
}

/**
 * The transactions of the selected year, search and filters, fetched page by page. A newer request makes
 * an older answer be ignored. `setTransactions` and `setOpenFollowUps` let the caller update a row in place.
 */
export function useTransactions(
  accountType: AccountType,
  year: string,
  search: string,
  followUpFilter: FollowUpFilter,
  selectedCategory: SelectedCategory | null,
  setError: (message: string) => void,
) {
  const [loadedTransactions, setTransactions] = useState<Transaction[]>([]);
  const [totalTransactions, setTotalTransactions] = useState(0);
  /** The entity's open follow-ups, from the last transactions request; null when the server does not report it. */
  const [openFollowUps, setOpenFollowUps] = useState<number | null>(null);
  const transactionsRequest = useRef(0);

  const loadTransactions = useCallback(async (overrideYear?: string) => {
    const request = ++transactionsRequest.current;
    const effectiveYear = overrideYear ?? year;
    if (!effectiveYear) {
      setTransactions([]);
      setTotalTransactions(0);
      return;
    }
    const params = new URLSearchParams({ account_type: accountType, year: effectiveYear, q: search });
    if (followUpFilter !== "all") params.set("follow_up", followUpFilter);
    else if (selectedCategory) {
      if (selectedCategory.flow_type !== "own_transfer") {
        params.set("category_id", String(selectedCategory.category_id ?? "uncategorized"));
      }
      params.set("flow_type", selectedCategory.flow_type);
    }
    try {
      const items: Transaction[] = [];
      let total = 0;
      let openCount: number | null = null;
      do {
        params.set("limit", String(TRANSACTION_PAGE_SIZE));
        params.set("offset", String(items.length));
        const page = await api<{ total: number; items: Transaction[]; open_follow_ups?: number }>(`/api/transactions?${params}`);
        if (request !== transactionsRequest.current) return;
        total = page.total;
        openCount = page.open_follow_ups ?? null;
        if (page.items.length === 0 && items.length < total) {
          throw new Error("Could not load all matching transactions.");
        }
        // An older server sends no annotation fields; they mean no note and no follow-up.
        items.push(...page.items.map((item) => ({ ...item, note: item.note ?? "", follow_up: item.follow_up ?? null, follow_up_at: item.follow_up_at ?? null })));
      } while (items.length < total);
      setTransactions(items);
      setTotalTransactions(total);
      setOpenFollowUps(openCount);
    } catch (cause) {
      if (request !== transactionsRequest.current) return;
      setError(errorMessage(cause, "Could not load transactions"));
    }
  }, [accountType, followUpFilter, search, selectedCategory, year]);

  return { loadedTransactions, setTransactions, totalTransactions, openFollowUps, setOpenFollowUps, loadTransactions };
}

/** The balance history of the selected year; `version` reloads it after data changes. */
export function useBalanceHistory(
  accountType: AccountType,
  year: string,
  forecastYears: string[],
  version: number,
  setError: (message: string) => void,
) {
  const [balanceHistory, setBalanceHistory] = useState<BalanceHistory | null>(null);
  useEffect(() => {
    // A forecast year has no history to load; the graph draws the forecast alone.
    if (!year || forecastYears.includes(year)) {
      setBalanceHistory(null);
      return;
    }
    let current = true;
    api<BalanceHistory>(`/api/balance-history?account_type=${accountType}&year=${year}`)
      .then((history) => current && setBalanceHistory(history))
      .catch((cause) => current && setError(errorMessage(cause, "Could not load balance history")));
    return () => {
      current = false;
    };
  }, [accountType, version, forecastYears, year]);
  return balanceHistory;
}

/** The budgets of the selected entity and year, as `[]` until they are loaded for that selection. */
export function useBudgets(accountType: AccountType, year: string, version: number) {
  /** Budgets loaded for one entity and year; they only count while that is still the selection. */
  const [loadedBudgets, setLoadedBudgets] = useState<{ key: string; items: Budget[] }>({ key: "", items: [] });
  // A server without budgets answers with the app page instead of JSON; that is the same as having none.
  useEffect(() => {
    let current = true;
    if (!year) return;
    const key = `${accountType}\u0000${year}`;
    api<Budget[]>(`/api/budgets?account_type=${accountType}&year=${year}`)
      .then((items) => (Array.isArray(items) ? items : []), () => [])
      .then((items) => current && setLoadedBudgets({ key, items }));
    return () => {
      current = false;
    };
  }, [accountType, version, year]);
  return loadedBudgets.key === `${accountType}\u0000${year}` ? loadedBudgets.items : [];
}

/**
 * The forecast-versus-actual comparison, which only counts while its entity is the active one.
 * It follows the forecast: computing the forecast is what saves the month's snapshot. Kids has none.
 */
export function useForecastVsActual(accountType: AccountType, showForecast: boolean, loadedForecast: Forecast | null) {
  /** The comparison of one entity; it only counts while that entity is the active one. */
  const [loadedVsActual, setLoadedVsActual] = useState<ForecastVsActual | null>(null);
  useEffect(() => {
    if (!showForecast || accountType === "kids" || !loadedForecast) {
      setLoadedVsActual(null);
      return;
    }
    let current = true;
    void fetchForecastVsActual(accountType).then((next) => { if (current) setLoadedVsActual(next); });
    return () => { current = false; };
  }, [accountType, showForecast, loadedForecast]);
  return showForecast && loadedVsActual?.account_type === accountType ? loadedVsActual : null;
}
