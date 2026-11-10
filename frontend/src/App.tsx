import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AccountType,
  Annotation,
  FollowUpFilter,
  FlowType,
  SelectedCategory,
  SimilarTransactions,
  Transaction,
} from "./types";
import { ACCOUNT_TYPES } from "./types";
import { api, errorMessage } from "./api";
import { isEditableTarget, useEscapeKey } from "./hooks";
import { BalanceChart } from "./BalanceChart";
import { CategoryCellEditor, type CategoryChoice } from "./CategoryCellEditor";
import { RulesView } from "./RulesView";
import { CategoriesView } from "./CategoriesView";
import { TriageView } from "./TriageView";
import { RecurringView } from "./RecurringView";
import { PlanView } from "./PlanView";
import { forecastOnlyYears } from "./forecast";
import {
  useBalanceHistory, useBudgets, useForecastVsActual, useReport, useTransactions, yearsOf,
} from "./dataHooks";
import { Toast, useToast } from "./Toast";
import { Dialog } from "./Dialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { AccountsView } from "./AccountsView";
import { BackupsView } from "./Backups";
import { ImportFileInput, useImportFlow } from "./Imports";
import { Footer, TopBar } from "./TopBar";
import type { Section, View } from "./TopBar";
import { SelectionBar, TransactionsPanel, isSelectable } from "./TransactionsPanel";
import { YearOverview } from "./YearOverview";
import { defaultSort, nextSort, sortTransactions, type SortKey, type TransactionSort } from "./transactionSort";

const UNCATEGORIZED = "Uncategorized";
const SHOW_FORECAST_KEY = "pfm.showForecast";
/** The remembered forecast switch; on unless turned off, and on when storage is unavailable. */
const readShowForecast = () => {
  try {
    return localStorage.getItem(SHOW_FORECAST_KEY) !== "off";
  } catch {
    return true;
  }
};
const writeShowForecast = (shown: boolean) => {
  try {
    localStorage.setItem(SHOW_FORECAST_KEY, shown ? "on" : "off");
  } catch {
    // Not remembered in a private window; the switch still works for this page.
  }
};

export function App() {
  const [accountType, setAccountType] = useState<AccountType>("personal");
  const { toast, setNotice, setError, dismiss: dismissToast } = useToast();
  const [year, setYear] = useState("");
  const { summaries, previousYearToDate, months, loadedForecast, categories, imports, refresh, reloadForecast } =
    useReport(accountType, setYear, setError);
  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<SelectedCategory | null>(null);
  /** The year's transactions, or the open or done follow-ups of all years. */
  const [followUpFilter, setFollowUpFilter] = useState<FollowUpFilter>("all");
  const { loadedTransactions, setTransactions, totalTransactions, openFollowUps, setOpenFollowUps, loadTransactions } =
    useTransactions(accountType, year, search, followUpFilter, selectedCategory, setError);
  /** The sort picked from the table header, for the follow-up filter it was picked in; another filter starts in its own order. */
  const [sortChoice, setSortChoice] = useState<{ filter: FollowUpFilter; sort: TransactionSort } | null>(null);
  const chosenSort = sortChoice?.filter === followUpFilter ? sortChoice.sort : null;
  /** The listed transactions in the shown order, which arrow keys, ranges and advancing follow. */
  const transactions = useMemo(() => sortTransactions(loadedTransactions, chosenSort), [loadedTransactions, chosenSort]);
  const [activeTransactionId, setActiveTransactionId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  /** Transactions ticked in the search results, for setting one category on all of them. */
  // The row where Shift+arrow started a range; the range runs from it to the active row.
  const [rangeAnchorId, setRangeAnchorId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  /** The category chosen in the selection bar for the ticked transactions, and whether its picker is open. */
  const [bulkChoice, setBulkChoice] = useState<CategoryChoice | null>(null);
  const [bulkPicking, setBulkPicking] = useState(false);
  const [similar, setSimilar] = useState<{ transactionId: number; data: SimilarTransactions } | null>(null);
  /** After a category change: other transactions of the same merchant a rule could categorize too. */
  const [ruleOffer, setRuleOffer] = useState<{
    transactionId: number;
    merchant: string;
    categoryId: number;
    categoryName: string;
    count: number;
    flowType: FlowType;
  } | null>(null);
  /** The row to select once the list reloads after a category change. */
  const pendingAdvance = useRef<{ nextId: number | null; index: number } | null>(null);
  const [savingCategory, setSavingCategory] = useState(false);
  const [balanceHistoryVersion, setBalanceHistoryVersion] = useState(0);
  const [showBalanceHistory, setShowBalanceHistory] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  /** The overview, or one of the full-page views (triage, forecast, rules, categories, accounts) that replace it. */
  const [view, setView] = useState<View>("overview");
  /** The part of the Forecast or Accounts page in use, set by a click in it: what N and the arrow keys act on. */
  const [section, setSection] = useState<Section>("plan");
  /** Raised by N in the Forecast page's recurring payments and the Accounts page's backups. */
  const [recurringSignal, setRecurringSignal] = useState(0);
  const [backupSignal, setBackupSignal] = useState(0);
  const [rulesFilter, setRulesFilter] = useState("");
  const [newRuleText, setNewRuleText] = useState("");
  const [openedRuleId, setOpenedRuleId] = useState<number | undefined>();
  /** Raised by the N shortcut; the current full-page view starts a new item when it changes. */
  const [newItemSignal, setNewItemSignal] = useState(0);
  const transactionSearchInput = useRef<HTMLInputElement>(null);
  const transactionRowRefs = useRef(new Map<number, HTMLTableRowElement>());
  const noteInput = useRef<HTMLInputElement>(null);
  // The transaction whose note field is open while it has no note yet.
  const [noteOpenId, setNoteOpenId] = useState<number | null>(null);

  const forecast = loadedForecast?.account_type === accountType ? loadedForecast : null;
  /** Whether the year table, the graph and the year tabs show the forecast (the top-bar switch, S); remembered per browser. */
  const [showForecast, setShowForecast] = useState(() => readShowForecast());
  const toggleForecast = () => setShowForecast((shown) => {
    writeShowForecast(!shown);
    return !shown;
  });
  // With projections off, everything renders as it does without a forecast; the Plan view keeps its own.
  const shownForecast = showForecast ? forecast : null;
  // The comparison follows the forecast: computing the forecast is what saves the month's snapshot. Kids has none.
  const vsActual = useForecastVsActual(accountType, showForecast, loadedForecast);
  /** Which recurring item the Forecast page's comparison asked to open; the nonce makes a repeated request count. */
  const [recurringTarget, setRecurringTarget] = useState<{ id: number | null; nonce: number } | null>(null);
  const reportYears = useMemo(() => yearsOf(months), [months]);
  // Years the forecast reaches beyond the data (next year) can be shown too, as forecast years.
  const forecastYears = useMemo(() => forecastOnlyYears(shownForecast, reportYears), [shownForecast, reportYears]);
  const years = useMemo(() => [...reportYears, ...forecastYears], [reportYears, forecastYears]);
  // Turning projections off hides a forecast year; move to the latest year with data.
  useEffect(() => {
    if (year && reportYears.length && !years.includes(year)) setYear(reportYears.at(-1)!);
  }, [year, years, reportYears]);
  const yearSummaries = useMemo(
    () => summaries.filter((summary) => summary.month.startsWith(`${year}-`)),
    [summaries, year],
  );
  const previousYearSummaries = useMemo(
    () => summaries.filter((summary) => summary.month.startsWith(`${Number(year) - 1}-`)),
    [summaries, year],
  );
  /** Transactions per category in the selected year, across flows; null is Uncategorized. */
  const yearCategoryCounts = useMemo(() => {
    const counts = new Map<number | null, number>();
    for (const summary of yearSummaries) {
      for (const item of summary.categories) {
        counts.set(item.category_id, (counts.get(item.category_id) ?? 0) + item.transaction_count);
      }
    }
    return counts;
  }, [yearSummaries]);
  /** Each category's total in the selected year in euros, net of refunds; Uncategorized has no budget and is left out. */
  const yearCategoryTotals = useMemo(() => {
    const cents = new Map<number, number>();
    for (const summary of yearSummaries) {
      for (const item of summary.categories) {
        if (item.category_id !== null) {
          cents.set(item.category_id, (cents.get(item.category_id) ?? 0) + Math.round(Number(item.amount) * 100));
        }
      }
    }
    return new Map([...cents].map(([id, amount]) => [id, amount / 100]));
  }, [yearSummaries]);
  const uncategorizedId = categories.find((category) => category.name === UNCATEGORIZED)?.id ?? null;
  const categoryChoices: CategoryChoice[] = useMemo(() => [
    { id: uncategorizedId, name: UNCATEGORIZED },
    ...categories.filter((category) => category.id !== uncategorizedId),
  ], [categories, uncategorizedId]);
  const emptyCategories = categories.filter((category) =>
    category.id !== uncategorizedId && !yearCategoryCounts.has(category.id));
  const hasYearData = yearSummaries.length > 0 || forecastYears.includes(year);
  const editingTransaction = transactions.find((transaction) => transaction.id === editingId);

  /** Reload everything and show the latest year, after imports change the data. */
  const reloadToLatestYear = async () => {
    setBalanceHistoryVersion((version) => version + 1);
    const report = await refresh();
    const latestYear = report?.months.at(-1)?.slice(0, 4) || year;
    setYear(latestYear);
    await loadTransactions(latestYear);
  };

  /** Show the restored data everywhere: leave Rules and Categories, which may hold stale data, and reload. */
  const reloadAfterRestore = async () => {
    setView("overview");
    setSelectedCategory(null);
    setEditingId(null);
    setActiveTransactionId(null);
    setRuleOffer(null);
    await reloadToLatestYear();
  };

  const importFlow = useImportFlow({
    onImported: async (importedType) => {
      if (importedType !== accountType) {
        setBalanceHistoryVersion((version) => version + 1);
        setAccountType(importedType);
        setFollowUpFilter("all");
        setSearch("");
        setSelectedCategory(null);
      } else {
        await reloadToLatestYear();
      }
    },
    onUndone: reloadToLatestYear,
    onError: setError,
    onNotice: setNotice,
  });

  // Staged previews are committed in the accounts view, so show it whenever files have just been chosen.
  const hasPreviews = importFlow.previews.length > 0;
  useEffect(() => {
    if (!hasPreviews) return;
    setView("accounts");
    setSection("accounts");
  }, [hasPreviews]);

  // A file input that is always mounted, so I and N can open the chooser from the keydown itself: browsers only
  // allow that inside a user gesture, and the accounts view that holds the Import files button may not exist yet.
  const importInput = useRef<HTMLInputElement>(null);
  const chooseImportFiles = () => {
    if (!importFlow.busy) importInput.current?.click();
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void loadTransactions();
  }, [loadTransactions]);

  const balanceHistory = useBalanceHistory(accountType, year, forecastYears, balanceHistoryVersion, setError);
  const budgets = useBudgets(accountType, year, balanceHistoryVersion);

  useEffect(() => {
    const visible = (id: number | null) => id !== null && transactions.some((transaction) => transaction.id === id);
    if (activeTransactionId !== null && !visible(activeTransactionId)) setActiveTransactionId(null);
    if (rangeAnchorId !== null && !visible(rangeAnchorId)) setRangeAnchorId(null);
    if (editingId !== null && !visible(editingId)) setEditingId(null);
  }, [activeTransactionId, editingId, rangeAnchorId, transactions]);

  // A selection belongs to one search and category filter in one entity and year; it is gone when any of them changes.
  useEffect(() => {
    setSelectedIds((current) => (current.size ? new Set() : current));
    setRangeAnchorId(null);
  }, [search, selectedCategory, followUpFilter, accountType, year]);

  // Selected transactions that are no longer listed drop out of the selection.
  useEffect(() => {
    setSelectedIds((current) => {
      const listed = new Set(transactions.map((transaction) => transaction.id));
      const kept = [...current].filter((id) => listed.has(id));
      return kept.length === current.size ? current : new Set(kept);
    });
  }, [transactions]);

  // Select the next transaction once the list reloads after a category change, so a
  // keyboard user can continue with Enter on the following row.
  useEffect(() => {
    const advance = pendingAdvance.current;
    if (!advance) return;
    pendingAdvance.current = null;
    const next = transactions.find((transaction) => transaction.id === advance.nextId)
      ?? transactions[Math.min(advance.index, transactions.length - 1)];
    if (!next) return;
    setActiveTransactionId(next.id);
    requestAnimationFrame(() => transactionRowRefs.current.get(next.id)?.focus());
  }, [transactions]);

  useEffect(() => {
    if (editingId === null) return;
    let current = true;
    api<SimilarTransactions>(`/api/transactions/${editingId}/similar`)
      .then((data) => current && setSimilar({ transactionId: editingId, data }))
      .catch(() => current && setSimilar(null));
    return () => {
      current = false;
    };
  }, [editingId]);

  useEscapeKey(showBalanceHistory, () => setShowBalanceHistory(false));
  useEscapeKey(showShortcuts, () => setShowShortcuts(false));
  // A dialog over the page (such as the corporate tax advice) takes Escape; it closes itself.
  useEscapeKey(view !== "overview", () => {
    if (!document.querySelector('[role="dialog"][aria-modal="true"]')) setView("overview");
  });
  const resetRulesContext = () => {
    setRulesFilter("");
    setNewRuleText("");
    setOpenedRuleId(undefined);
  };
  const toggleView = (next: Exclude<View, "overview">) => {
    resetRulesContext();
    if (next === "forecast" || next === "accounts") setSection(next === "forecast" ? "plan" : "accounts");
    setView((current) => (current === next ? "overview" : next));
  };
  /** Clicking in a two-part page makes that part the one N and the arrow keys act on. */
  const pickSection = (event: React.PointerEvent, inner: Section, outer: Section) =>
    setSection(event.target instanceof Element && event.target.closest(`#section-${inner}`) ? inner : outer);

  const switchAccountType = (kind: AccountType) => {
    setAccountType(kind);
    setFollowUpFilter("all");
    setSearch("");
    setSelectedCategory(null);
  };

  /** Filters the transactions to a category; a new selection starts without a search. */
  const selectCategory = (category: SelectedCategory | null) => {
    if (category) {
      setSearch("");
      setFollowUpFilter("all");
    }
    setSelectedCategory(category);
  };

  /** Switches the table between the year and the follow-ups; the follow-up lists have no category filter. */
  const chooseFollowUpFilter = (filter: FollowUpFilter) => {
    if (filter !== "all") setSelectedCategory(null);
    setFollowUpFilter(filter);
  };

  /**
   * Saves a note or follow-up change and updates the row from the answer. An annotation never changes
   * categories or totals, so nothing else reloads; the row stays in a follow-up list until the next reload.
   */
  const annotate = async (transaction: Transaction, change: Partial<Pick<Annotation, "note" | "follow_up">>) => {
    setError("");
    try {
      const saved = await api<Annotation>(`/api/transactions/${transaction.id}/annotation`, { method: "PATCH", json: change });
      setTransactions((current) => current.map((item) => (item.id === transaction.id ? { ...item, ...saved } : item)));
      setOpenFollowUps((count) => (count === null ? null
        : Math.max(0, count + Number(saved.follow_up === "open") - Number(transaction.follow_up === "open"))));
      return true;
    } catch (cause) {
      setError(errorMessage(cause, "Could not save the note"));
      return false;
    }
  };

  const activeTransaction = transactions.find((item) => item.id === activeTransactionId);

  /** F: switches between all transactions and the follow-ups. */
  const toggleFollowUps = () => chooseFollowUpFilter(followUpFilter === "open" ? "all" : "open");

  /** Shows the transaction's note field, even while it is empty, and focuses it. */
  const openNote = (transaction: Transaction) => {
    setNoteOpenId(transaction.id);
    requestAnimationFrame(() => noteInput.current?.focus());
  };

  /** M: flags the active transaction and goes to its note, or removes the flag. */
  const toggleFlag = () => {
    if (!activeTransaction) return false;
    if (activeTransaction.follow_up) {
      void annotate(activeTransaction, { follow_up: null });
      return;
    }
    void annotate(activeTransaction, { follow_up: "open" }).then((saved) => {
      if (saved) openNote(activeTransaction);
    });
  };

  /** D: marks the active transaction's open follow-up done, or reopens a done one. */
  const toggleDone = () => {
    if (!activeTransaction) return false;
    if (!activeTransaction.follow_up) {
      setNotice("This transaction has no follow-up.");
      return;
    }
    void annotate(activeTransaction, { follow_up: activeTransaction.follow_up === "open" ? "done" : "open" });
  };

  const openCategoryEditor = (transaction: Transaction) => {
    setActiveTransactionId(transaction.id);
    setEditingId(transaction.id);
  };

  const closeCategoryEditor = () => {
    if (editingId === null) return;
    const rowId = editingId;
    setEditingId(null);
    requestAnimationFrame(() => transactionRowRefs.current.get(rowId)?.focus());
  };

  /** Moves the active transaction; `extend` (Shift) keeps a range anchored where it started. */
  const moveActiveTransaction = (step: 1 | -1, extend = false) => {
    const index = transactions.findIndex((transaction) => transaction.id === activeTransactionId);
    const nextIndex = index < 0
      ? (step === 1 ? 0 : transactions.length - 1)
      : Math.max(0, Math.min(transactions.length - 1, index + step));
    const next = transactions[nextIndex];
    setActiveTransactionId(next.id);
    setRangeAnchorId(extend ? (rangeAnchorId ?? (index < 0 ? next.id : activeTransactionId)) : null);
    transactionRowRefs.current.get(next.id)?.focus();
  };

  const isUncategorized = (transaction: Transaction) =>
    transaction.category_id === null || transaction.category_id === uncategorizedId;

  /** Opens the next uncategorized transaction after the active one, wrapping around, in its category editor. */
  const editNextUncategorized = () => {
    const start = transactions.findIndex((transaction) => transaction.id === activeTransactionId);
    for (let offset = 1; offset <= transactions.length; offset += 1) {
      const next = transactions[(start + offset + transactions.length) % transactions.length];
      // Own-account moves and itemized card payments have no category to set.
      if (!isUncategorized(next) || next.own_transfer || next.itemized) continue;
      transactionRowRefs.current.get(next.id)?.focus();
      openCategoryEditor(next);
      return;
    }
    setNotice("No uncategorized transactions here.");
  };

  /** The listed transactions from the range anchor to the active one, inclusive; empty unless it spans several. */
  const rangeIds = (() => {
    const from = transactions.findIndex((transaction) => transaction.id === rangeAnchorId);
    const to = transactions.findIndex((transaction) => transaction.id === activeTransactionId);
    if (from < 0 || to < 0 || from === to) return new Set<number>();
    return new Set(transactions.slice(Math.min(from, to), Math.max(from, to) + 1).map((transaction) => transaction.id));
  })();

  // An empty selection starts the next batch without a category.
  useEffect(() => {
    if (selectedIds.size) return;
    setBulkChoice(null);
    setBulkPicking(false);
  }, [selectedIds.size]);

  /** Puts focus back on the active row (or the first) unless it already sits on something, so shortcuts keep working. */
  const refocusTable = () => requestAnimationFrame(() => {
    if (document.activeElement && document.activeElement !== document.body) return;
    const row = transactionRowRefs.current.get(activeTransactionId ?? transactions[0]?.id ?? -1);
    row?.focus();
  });

  /** Enter with rows ticked: open the bulk picker, or apply the category already chosen. */
  const enterOnSelection = () => {
    if (!selectedIds.size) return false;
    if (savingCategory) return;
    if (bulkChoice?.id != null && !bulkPicking) void setCategoryOfSelected(bulkChoice.id, bulkChoice.name);
    else setBulkPicking(true);
  };

  const toggleSelected = (id: number) => setSelectedIds((current) => {
    const next = new Set(current);
    if (!next.delete(id)) next.add(id);
    return next;
  });

  const selectAllListed = (select: boolean) =>
    setSelectedIds(select ? new Set(transactions.filter(isSelectable).map((transaction) => transaction.id)) : new Set());

  /** Toggles the active transaction, or the range's selectable rows. */
  const toggleActiveSelected = () => {
    if (rangeIds.size) {
      const rows = transactions.filter((item) => rangeIds.has(item.id) && isSelectable(item));
      const allChecked = rows.every((item) => selectedIds.has(item.id));
      setSelectedIds((current) => {
        const next = new Set(current);
        rows.forEach((item) => (allChecked ? next.delete(item.id) : next.add(item.id)));
        return next;
      });
      return;
    }
    const transaction = transactions.find((item) => item.id === activeTransactionId);
    if (!transaction || !isSelectable(transaction)) return false;
    toggleSelected(transaction.id);
  };

  const openRule = (ruleId: number) => {
    setRulesFilter("");
    setNewRuleText("");
    setOpenedRuleId(ruleId);
    setView("rules");
  };

  /** Opens the rule that set the active transaction's category. */
  const openActiveTransactionRule = () => {
    const transaction = transactions.find((item) => item.id === activeTransactionId);
    if (!transaction) return false;
    if (transaction.category_source === "rule" && transaction.category_rule_id !== null
      && transaction.category_rule_position !== null) {
      openRule(transaction.category_rule_id);
    } else {
      setNotice("This category wasn't set by a rule.");
    }
  };

  const focusSearch = () => {
    transactionSearchInput.current?.focus();
    transactionSearchInput.current?.select();
  };

  const moveYear = (step: 1 | -1) => {
    const index = years.indexOf(year);
    if (index < 0) return false;
    const next = years[index + step];
    if (next) setYear(next);
  };

  // Global keyboard shortcuts; the shortcuts dialog (K) lists them. An action returning
  // false did not apply, so the key keeps its default behavior.
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (isEditableTarget(event.target)) return;
      if (editingId !== null) return;
      const key = event.key.toLowerCase();
      // Dialogs only react to their own toggle keys (and Escape, handled separately).
      // A modal dialog inside a page takes every key.
      if (!showShortcuts && !showBalanceHistory && document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      if (showShortcuts && key !== "k") return;
      if (showBalanceHistory && key !== "g" && key !== "k") return;
      const anywhere: Record<string, () => boolean | void> = {
        // Escape ends a range first, then unselects the selected transaction, then clears the category filter.
        // Escape deselects ticked transactions (and any range) first, then the active row, then the filter.
        escape: () => {
          if (selectedIds.size) {
            setSelectedIds(new Set());
            setRangeAnchorId(null);
            return;
          }
          if (rangeAnchorId !== null) {
            setRangeAnchorId(null);
            if (rangeIds.size) return;
          }
          if (activeTransactionId !== null) {
            setActiveTransactionId(null);
            if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
            return;
          }
          if (!selectedCategory) return false;
          setSelectedCategory(null);
        },
        "/": focusSearch,
        s: toggleForecast,
      };
      const elsewhere: Record<string, () => boolean | void> = {
        arrowleft: () => moveYear(-1),
        arrowright: () => moveYear(1),
        arrowdown: () => (transactions.length ? moveActiveTransaction(1, event.shiftKey) : false),
        arrowup: () => (transactions.length ? moveActiveTransaction(-1, event.shiftKey) : false),
        g: () => setShowBalanceHistory((visible) => !visible),
        e: () => switchAccountType(ACCOUNT_TYPES[(ACCOUNT_TYPES.indexOf(accountType) + 1) % ACCOUNT_TYPES.length]),
        a: () => toggleView("accounts"),
        k: () => {
          setShowBalanceHistory(false);
          setShowShortcuts((visible) => !visible);
        },
        i: () => {
          resetRulesContext();
          setView("accounts");
          setSection("accounts");
          chooseImportFiles();
        },
        r: () => toggleView("rules"),
        c: () => toggleView("categories"),
        t: () => toggleView("triage"),
        f: () => toggleView("forecast"),
        u: editNextUncategorized,
        x: toggleActiveSelected,
        enter: enterOnSelection,
        o: openActiveTransactionRule,
        // Shift+M shows the follow-ups (or all transactions again); M flags the selected one.
        m: () => (event.shiftKey ? toggleFollowUps() : toggleFlag()),
        d: toggleDone,
        n: () => {
          if (view === "overview") return false;
          if (view === "accounts" && section === "backups") setBackupSignal((signal) => signal + 1);
          else if (view === "accounts") chooseImportFiles();
          else if (view === "forecast" && section === "recurring") setRecurringSignal((signal) => signal + 1);
          else setNewItemSignal((signal) => signal + 1);
        },
      };
      // The full-page views replace the overview; only switching views, accounts and dialogs applies there.
      if (view !== "overview" && !["t", "f", "c", "r", "a", "i", "e", "g", "k", "n", "s"].includes(key)) return;
      const action = anywhere[key] ?? elsewhere[key];
      if (action && action() !== false) event.preventDefault();
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  });

  const chooseCategory = async (transaction: Transaction, categoryId: number | null) => {
    setSavingCategory(true);
    setError("");
    try {
      await api(`/api/transactions/${transaction.id}`, { method: "PATCH", json: { category_id: categoryId } });
      const index = transactions.findIndex((item) => item.id === transaction.id);
      pendingAdvance.current = { nextId: transactions[index + 1]?.id ?? null, index };
      setEditingId(null);
      const others = similar?.transactionId === transaction.id
        ? similar.data
        : await api<SimilarTransactions>(`/api/transactions/${transaction.id}/similar`);
      const category = categories.find((item) => item.id === categoryId);
      const count = others.others.filter((other) => other.category_id !== categoryId).length;
      // A rule on the merchant alone would also catch its other kinds of transactions; only offer it when there are none.
      const merchantRuleFits = others.merchant_count === others.others.length;
      setRuleOffer(category && category.id !== uncategorizedId && others.merchant && merchantRuleFits && count > 0 ? {
        transactionId: transaction.id,
        merchant: others.merchant,
        categoryId: category.id,
        categoryName: category.name,
        count,
        flowType: (category.is_income ? "income" : category.rule_flow_type) ?? (Number(transaction.amount) > 0 ? "income" : "expense"),
      } : null);
      await Promise.all([refresh(), loadTransactions()]);
    } catch (cause) {
      setError(errorMessage(cause, "Could not update transaction category"));
    } finally {
      setSavingCategory(false);
    }
  };

  const setCategoryOfSelected = async (categoryId: number, categoryName: string) => {
    setSavingCategory(true);
    setError("");
    try {
      const result = await api<{ updated: number }>("/api/transactions/bulk-category", {
        method: "POST",
        json: { account_type: accountType, transaction_ids: [...selectedIds], category_id: categoryId },
      });
      setSelectedIds(new Set());
      setRuleOffer(null);
      setNotice(`Set ${result.updated} transaction${result.updated === 1 ? "" : "s"} to ${categoryName}.`);
      await Promise.all([refresh(), loadTransactions()]);
    } catch (cause) {
      setError(errorMessage(cause, "Could not set the category"));
    } finally {
      setSavingCategory(false);
      refocusTable();
    }
  };

  const makeRuleFromOffer = async () => {
    if (!ruleOffer) return;
    setError("");
    try {
      const rule = await api<{ id: number }>("/api/category-rules", {
        method: "POST",
        json: {
          account_type: accountType,
          category_id: ruleOffer.categoryId,
          match_text: ruleOffer.merchant,
          match_fields: ["merchant"],
          match_mode: "exact",
          flow_type: ruleOffer.flowType,
        },
      });
      const result = await api<{ updated: number }>(`/api/category-rules/${rule.id}/run`, {
        method: "POST",
        json: { account_type: accountType },
      });
      setRuleOffer(null);
      setNotice(`Added a rule for “${ruleOffer.merchant}”; categorized ${result.updated} transaction${result.updated === 1 ? "" : "s"} as ${ruleOffer.categoryName}.`);
      setBalanceHistoryVersion((version) => version + 1);
      await Promise.all([refresh(), loadTransactions()]);
    } catch (cause) {
      setError(errorMessage(cause, "Could not make the rule"));
    }
  };

  return (
    <main className="app-shell">
      <TopBar
        accountType={accountType}
        onAccountType={switchAccountType}
        years={years}
        year={year}
        onYear={setYear}
        forecastYears={forecastYears}
        showForecast={showForecast}
        onToggleForecast={toggleForecast}
        onAccounts={() => toggleView("accounts")}
        view={view}
        onTriage={() => toggleView("triage")}
        onRules={() => toggleView("rules")}
        onCategories={() => toggleView("categories")}
        onForecast={() => toggleView("forecast")}
      />

      <Toast toast={toast} onDismiss={dismissToast} />
      <div hidden>
        <ImportFileInput className="choose-file" inputRef={importInput} onFiles={(files) => void importFlow.previewFiles(files)}>
          {null}
        </ImportFileInput>
      </div>

      {view === "triage" ? (
        <TriageView
          key={accountType}
          accountType={accountType}
          categories={categories}
          onNotice={setNotice}
          onError={setError}
          onChanged={async () => {
            setBalanceHistoryVersion((version) => version + 1);
            await Promise.all([refresh(), loadTransactions()]);
          }}
          onClose={() => setView("overview")}
        />
      ) : view === "forecast" ? (
        <div onPointerDown={(event) => pickSection(event, "recurring", "plan")}>
          <PlanView
            key={accountType}
            accountType={accountType}
            categories={categories}
            forecast={forecast}
            vsActual={vsActual}
            onOpenRecurring={(id) => setRecurringTarget((target) => ({ id, nonce: (target?.nonce ?? 0) + 1 }))}
            onChanged={() => void reloadForecast()}
            onNotice={setNotice}
            onError={setError}
            onClose={() => setView("overview")}
            newItemSignal={newItemSignal}
            recurring={(
              <div id="section-recurring">
                <RecurringView
                  key={accountType}
                  accountType={accountType}
                  categories={categories}
                  onNotice={setNotice}
                  onError={setError}
                  onChanged={() => void reloadForecast()}
                  newItemSignal={recurringSignal}
                  active={section === "recurring"}
                  openRequest={recurringTarget}
                />
              </div>
            )}
          />
        </div>
      ) : view === "rules" ? (
        <RulesView
          key={`${accountType}\u0000${rulesFilter}\u0000${newRuleText}\u0000${openedRuleId ?? ""}`}
          accountType={accountType}
          initialFilter={rulesFilter}
          initialNewRuleText={newRuleText}
          initialSelectedId={openedRuleId}
          newItemSignal={newItemSignal}
          onNotice={setNotice}
          onError={setError}
          onChanged={async () => {
            setBalanceHistoryVersion((version) => version + 1);
            await Promise.all([refresh(), loadTransactions()]);
          }}
          onClose={() => setView("overview")}
        />
      ) : view === "categories" ? (
        <CategoriesView
          key={accountType}
          accountType={accountType}
          year={year}
          budgets={budgets}
          yearTotals={yearCategoryTotals}
          newItemSignal={newItemSignal}
          onNotice={setNotice}
          onError={setError}
          onChanged={async () => {
            setBalanceHistoryVersion((version) => version + 1);
            await Promise.all([refresh(), loadTransactions()]);
          }}
          onEditRules={(category) => {
            setRulesFilter(category.name);
            setNewRuleText("");
            setOpenedRuleId(undefined);
            setView("rules");
          }}
          onClose={() => setView("overview")}
        />
      ) : view === "accounts" ? (
        <div className="stacked-view" onPointerDown={(event) => pickSection(event, "backups", "accounts")}>
          <AccountsView flow={importFlow} onClose={() => setView("overview")} active={section === "accounts"} />
          <div id="section-backups">
            <BackupsView
              imports={imports}
              newItemSignal={backupSignal}
              onNotice={setNotice}
              onError={setError}
              onRestored={reloadAfterRestore}
              active={section === "backups"}
            />
          </div>
        </div>
      ) : hasYearData ? (
        <>
          <YearOverview
            year={year}
            summaries={yearSummaries}
            previousSummaries={previousYearSummaries}
            previousYearToDate={previousYearToDate}
            emptyCategories={emptyCategories}
            budgets={budgets}
            forecast={shownForecast}
            vsActual={vsActual}
            categories={categories}
            selectedCategory={selectedCategory}
            onSelectCategory={selectCategory}
            onBalanceHistory={() => setShowBalanceHistory(true)}
          />

          <TransactionsPanel
            year={year}
            transactions={transactions}
            total={totalTransactions}
            sort={chosenSort ?? defaultSort(followUpFilter)}
            onSort={(key: SortKey) => setSortChoice({ filter: followUpFilter, sort: nextSort(chosenSort ?? defaultSort(followUpFilter), key) })}
            search={search}
            onSearch={setSearch}
            searchInput={transactionSearchInput}
            selectedCategory={selectedCategory}
            onClearCategory={() => setSelectedCategory(null)}
            activeTransactionId={activeTransactionId}
            onActivate={setActiveTransactionId}
            onRowClick={(transactionId) => {
              setRangeAnchorId(null);
              setActiveTransactionId(transactionId);
            }}
            rangeIds={rangeIds}
            onEditCategory={openCategoryEditor}
            isUncategorized={isUncategorized}
            rowRefs={transactionRowRefs}
            onRuleFromSearch={() => {
              setRulesFilter("");
              setNewRuleText(search.trim());
              setOpenedRuleId(undefined);
              setView("rules");
            }}
            onOpenRule={openRule}
            selectedIds={selectedIds}
            onToggleSelected={toggleSelected}
            onSelectAll={selectAllListed}
            selectionBar={selectedIds.size ? (
              <SelectionBar
                count={selectedIds.size}
                choices={categoryChoices.filter((choice) => choice.id !== null)}
                chosen={bulkChoice}
                picking={bulkPicking}
                disabled={savingCategory}
                onChosen={(choice) => {
                  setBulkChoice(choice);
                  refocusTable();
                }}
                onPicking={(picking) => {
                  setBulkPicking(picking);
                  if (!picking) refocusTable();
                }}
                onApply={(categoryId, name) => void setCategoryOfSelected(categoryId, name)}
                onClear={() => setSelectedIds(new Set())}
              />
            ) : null}
            categoryEditor={editingTransaction ? {
              transactionId: editingTransaction.id,
              element: (
                <CategoryCellEditor
                  key={editingTransaction.id}
                  choices={categoryChoices}
                  currentCategoryId={editingTransaction.category_id ?? uncategorizedId}
                  suggestedCategoryId={similar?.transactionId === editingTransaction.id
                    ? similar.data.suggestion?.category_id ?? null
                    : null}
                  disabled={savingCategory}
                  onChoose={(categoryId) => void chooseCategory(editingTransaction, categoryId)}
                  onClose={closeCategoryEditor}
                />
              ),
            } : null}
            followUpFilter={followUpFilter}
            openFollowUps={openFollowUps}
            onFollowUpFilter={chooseFollowUpFilter}
            onAnnotate={annotate}
            noteInput={noteInput}
            noteOpenId={noteOpenId}
            onOpenNote={openNote}
            onCloseNote={() => setNoteOpenId(null)}
            ruleOffer={ruleOffer ? {
              transactionId: ruleOffer.transactionId,
              element: (
                <div className="rule-offer">
                  <span>
                    Also categorize {ruleOffer.count} other “{ruleOffer.merchant}” transaction{ruleOffer.count === 1 ? "" : "s"} as {ruleOffer.categoryName}?
                    <span className="muted"> Adds a rule for this merchant.</span>
                  </span>
                  <button className="button secondary" onClick={() => void makeRuleFromOffer()}>Make rule</button>
                  <button className="icon-button" aria-label="Dismiss rule suggestion" onClick={() => setRuleOffer(null)}>×</button>
                </div>
              ),
            } : null}
          />
        </>
      ) : (
        <section className="panel first-import">
          <div className="empty-illustration">↥</div>
          <h3>Your overview starts with an import</h3>
          <p className="muted">Choose bank or card exports (CSV or PDF). You’ll review the parsed transactions and assign each account as personal, business, or kids before anything is stored.</p>
          <ImportFileInput className="button primary choose-file" onFiles={(files) => void importFlow.previewFiles(files)}>
            Choose files
          </ImportFileInput>
        </section>
      )}

      {showBalanceHistory && hasYearData && (
        <Dialog
          title="Balance history"
          closeLabel="Close balance history"
          className="balance-history-dialog"
          backdropClassName="balance-history-backdrop"
          onClose={() => setShowBalanceHistory(false)}
        >
          <BalanceChart history={balanceHistory} forecast={shownForecast} vsActual={vsActual} year={year} />
        </Dialog>
      )}

      {showShortcuts && <ShortcutsDialog onClose={() => setShowShortcuts(false)} />}

      <Footer />
    </main>
  );
}
