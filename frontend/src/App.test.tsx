import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const summary = {
  month: "2026-03",
  income: "100.00",
  expenses: "-40.00",
  net: "60.00",
  own_transfers: "0.00",
  categories: [],
  account_balances: [],
  transaction_count: 0,
};

const transaction = (id: number, overrides: Record<string, unknown> = {}) => ({
  id,
  booking_date: "2026-03-01",
  value_date: "2026-03-01",
  amount: "-10.00",
  balance: null,
  currency: "EUR",
  merchant: `Merchant ${id}`,
  details: "",
  description: "",
  memo: "",
  counterparty: "",
  counterparty_iban: "",
  flow_type: "expense",
  category_id: 1,
  category: "Groceries",
  category_source: "default",
  category_rule: null,
  category_rule_id: null,
  category_rule_position: null,
  account: "KBC",
  own_transfer: false,
  itemized: false,
  note: "",
  follow_up: null,
  follow_up_at: null,
  ...overrides,
});
let transactionItems: ReturnType<typeof transaction>[] = [];
const planSettings = {
  personal_min_balance: "2000.00", business_buffer: "2000.00", payment_delay_months: 1, vat_rate: 21, vat_delay_months: 1,
  vat_day: 15, baseline_months: 6, baseline_weighted: false, one_off_factor: 3, salary_net_percent: 0, reimbursement_delay_days: 30,
  roles: {}, closed_categories: { personal: [], business: [], kids: [] },
};

beforeEach(() => {
  apiMock.mockReset();
  transactionItems = [];
  apiMock.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
    if (url.startsWith("/api/transactions/") && url.endsWith("/similar")) {
      return { merchant: "", merchant_count: 0, others: [], suggestion: null };
    }
    if (url.startsWith("/api/transactions")) return { total: transactionItems.length, items: transactionItems };
    if (url.startsWith("/api/balance-history")) return { points: [] };
    if (url.startsWith("/api/backups")) return [];
    return [];
  });
});

afterEach(cleanup);

const press = (key: string) => fireEvent.keyDown(window, { key });
const pressShiftM = () => fireEvent.keyDown(window, { key: "M", shiftKey: true });
const pressed = (name: string) => screen.getByRole("button", { name }).getAttribute("aria-pressed");

const renderApp = async () => {
  render(<App />);
  await screen.findByRole("button", { name: "2026" });
};

describe("Transaction sorting", () => {
  it("sorts from the Amount header, and arrow keys follow the shown order", async () => {
    transactionItems = [transaction(1, { amount: "-5.00" }), transaction(2, { amount: "-50.00" }), transaction(3, { amount: "20.00" })];
    await renderApp();
    await screen.findByText("Merchant 1");
    const merchants = () => screen.getAllByText(/^Merchant \d$/).map((cell) => cell.textContent);
    expect(merchants()).toEqual(["Merchant 1", "Merchant 2", "Merchant 3"]);
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    expect(merchants()).toEqual(["Merchant 3", "Merchant 1", "Merchant 2"]);
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    expect(merchants()).toEqual(["Merchant 2", "Merchant 1", "Merchant 3"]);
    press("ArrowDown");
    press("ArrowDown");
    expect(screen.getByRole("group", { name: "Selected transaction" })).toHaveTextContent("Merchant 1 ·");
    fireEvent.click(screen.getByRole("button", { name: /Date/ }));
    expect(merchants()).toEqual(["Merchant 1", "Merchant 2", "Merchant 3"]);
  });
});

describe("Forecast vs reality", () => {
  const vsActual = {
    account_type: "personal", today: "2026-12-05",
    months: [{
      month: "2026-02", snapshot_date: "2026-02-01", status: "complete",
      income: { forecast: "100.00", actual: "100.00", difference: "0.00" },
      expense: { forecast: "40.00", actual: "60.00", difference: "20.00" },
      balance: { forecast: "500.00", low: "400.00", high: "600.00", actual: "450.00", difference: "-50.00", miss: false },
      categories: [],
    }],
    pace: null, summary: { months_tracked: 1, typical_balance_miss: "50.00" },
    misses: [{ month: "2026-02", category_id: 5, category: "Internet", flow: "expense", forecast: "30.00", actual: "40.00", difference: "10.00", hint: "amount_changed", recurring_id: 7 }],
  };
  const recurringItem = {
    id: 7, status: "confirmed", merchant: "Fibre Co", kind: "Direct debit", cadence: "monthly", day_of_month: 3, category_id: 5,
    category: "Internet", amount: "-40.00", monthly_amount: "-40.00", amounts: [{ effective_date: "2026-01-01", amount: "-40.00" }],
    start_date: "2026-01-03", end_date: null, last_date: "2026-02-03", next_date: "2026-03-03", history: [], occurrences: 2, flags: [],
    linked: null, note: "",
  };
  const requested: string[] = [];
  beforeEach(() => {
    requested.length = 0;
    localStorage.removeItem("pfm.showForecast");
    apiMock.mockImplementation(async (url: string) => {
      requested.push(url);
      if (url.startsWith("/api/forecast/vs-actual")) return vsActual;
      if (url.startsWith("/api/forecast/settings")) return planSettings;
      if (url.startsWith("/api/forecast/salary")) return [];
      if (url.startsWith("/api/forecast?")) {
        return {
          account_type: "personal", today: "2026-03-10", accounts: [], opening: "500.00", year_ends: [], one_offs: [], assumptions: [],
          backtest: { origins: 0, horizons: [], baseline: {}, recommended: {} }, months: [],
        };
      }
      if (url.startsWith("/api/recurring/linkable") || url.startsWith("/api/recurring/groups")) return [];
      if (url.startsWith("/api/recurring")) {
        return { monthly: { income: "0.00", expense: "0.00", net: "0.00" }, items: [recurringItem], candidates: [], ignored: [] };
      }
      if (url.startsWith("/api/planned")) return [];
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
  });
  afterEach(() => localStorage.removeItem("pfm.showForecast"));

  it("shows the panel on the Forecast page, and its Amount changed hint opens that recurring payment", async () => {
    await renderApp();
    press("f");
    const panel = await screen.findByRole("region", { name: "Forecast vs reality" });
    expect(requested).toContain("/api/forecast/vs-actual?account_type=personal");
    fireEvent.click(within(panel).getByRole("button", { name: "Amount changed" }));
    const row = (await screen.findByText("Fibre Co", { selector: "strong" })).closest("tr")!;
    await waitFor(() => expect(row).toHaveAttribute("aria-selected", "true"));
    expect(await screen.findByRole("complementary", { name: "Recurring payment details" })).toBeInTheDocument();
  });

  it("asks for nothing and shows nothing when the Forecast switch is off", async () => {
    localStorage.setItem("pfm.showForecast", "off");
    await renderApp();
    expect(requested.some((url) => url.startsWith("/api/forecast/vs-actual"))).toBe(false);
    press("f");
    await screen.findByRole("region", { name: "Recurring payments" });
    expect(screen.queryByRole("region", { name: "Forecast vs reality" })).not.toBeInTheDocument();
    expect(document.querySelector(".year-diff")).toBeNull();
    expect(requested.some((url) => url.startsWith("/api/forecast/vs-actual"))).toBe(false);
  });

  it("never asks for Kids", async () => {
    await renderApp();
    press("e");
    press("e");
    await waitFor(() => expect(pressed("Kids")).toBe("true"));
    await screen.findByRole("button", { name: "2026" });
    expect(requested).not.toContain("/api/forecast/vs-actual?account_type=kids");
  });

  it("works against an older server without the endpoint", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/forecast/vs-actual")) throw new Error("Not Found");
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
    await renderApp();
    expect(document.querySelector(".year-diff")).toBeNull();
  });
});

describe("Global keyboard shortcuts", () => {
  it("E cycles personal, business, kids; P, L and B do nothing", async () => {
    await renderApp();
    expect(pressed("Personal")).toBe("true");
    press("e");
    expect(pressed("Business")).toBe("true");
    press("e");
    expect(pressed("Kids")).toBe("true");
    press("e");
    expect(pressed("Personal")).toBe("true");
    press("p");
    press("l");
    press("b");
    expect(pressed("Personal")).toBe("true");
    expect(screen.getByRole("button", { name: "Forecast" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Accounts" })).toHaveAttribute("aria-pressed", "false");
  });

  it("A opens the Accounts page with the backups under the accounts", async () => {
    await renderApp();
    press("a");
    expect(screen.getByRole("button", { name: "Accounts" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "Backups" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Accounts" })).toBeInTheDocument();
    press("a");
    expect(screen.getByRole("button", { name: "Accounts" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Backups" })).not.toBeInTheDocument();
  });

  it("A toggles the Accounts view", async () => {
    await renderApp();
    press("a");
    expect(screen.getByRole("button", { name: "Accounts" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "Accounts" })).toBeInTheDocument();
    press("a");
    expect(screen.getByRole("button", { name: "Accounts" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Accounts" })).not.toBeInTheDocument();
  });

  it("T toggles the Triage view", async () => {
    await renderApp();
    press("t");
    expect(screen.getByRole("button", { name: "Triage" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "Triage" })).toBeInTheDocument();
    press("r");
    expect(screen.getByRole("button", { name: "Triage" })).toHaveAttribute("aria-pressed", "false");
    press("t");
    press("t");
    expect(screen.getByRole("button", { name: "Triage" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Triage" })).not.toBeInTheDocument();
  });

  it("F toggles the Forecast page with its recurring payments", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/recurring")) {
        return url.startsWith("/api/recurring/linkable")
          ? []
          : { monthly: { income: "0.00", expense: "0.00", net: "0.00" }, items: [], candidates: [], ignored: [] };
      }
      if (url.startsWith("/api/forecast/settings")) return planSettings;
      if (url.startsWith("/api/forecast/salary")) return [];
      if (url.startsWith("/api/planned")) return [];
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
    await renderApp();
    press("f");
    expect(screen.getByRole("button", { name: "Forecast" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "Recurring payments" })).toBeInTheDocument();
    press("f");
    expect(screen.getByRole("button", { name: "Forecast" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Recurring payments" })).not.toBeInTheDocument();
  });

  it("on the Forecast page, N adds a planned item, or after a click in recurring payments a payment", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/recurring")) {
        return url.startsWith("/api/recurring/linkable") || url.startsWith("/api/recurring/groups")
          ? []
          : { monthly: { income: "0.00", expense: "0.00", net: "0.00" }, items: [], candidates: [], ignored: [] };
      }
      if (url.startsWith("/api/forecast/settings")) return planSettings;
      if (url.startsWith("/api/forecast/salary")) return [];
      if (url.startsWith("/api/planned")) return [];
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
    await renderApp();
    press("f");
    expect(screen.getByRole("button", { name: "Forecast" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "Planned items" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Recurring payments" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add planned item" })).not.toBeInTheDocument();
    press("n");
    expect(await screen.findByRole("form", { name: "Add planned item" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Add a recurring payment" })).not.toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("region", { name: "Recurring payments" }));
    press("n");
    expect(await screen.findByRole("complementary", { name: "Add a recurring payment" })).toBeInTheDocument();
    press("f");
    expect(screen.getByRole("button", { name: "Forecast" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Planned items" })).not.toBeInTheDocument();
  });

  it("offers next year as a forecast year when the forecast reaches it", async () => {
    const forecastMonth = (key: string) => ({
      month: key, partial: false, lines: [], actual: { income: "0.00", expense: "0.00" }, income: "0.00", expense: "0.00",
      net: "0.00", closing: "500.00", low: "400.00", high: "600.00", flags: [],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/forecast")) {
        return {
          account_type: "personal", today: "2026-03-10", accounts: [], opening: "500.00", year_ends: [], one_offs: [],
          assumptions: [], backtest: { origins: 0, horizons: [], baseline: {}, recommended: {} },
          months: [forecastMonth("2026-03"), forecastMonth("2027-01")],
        };
      }
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
    await renderApp();
    const tab = await screen.findByRole("button", { name: "2027" });
    expect(tab).toHaveAttribute("title", "2027 · forecast");
    fireEvent.click(tab);
    expect(pressed("2027")).toBe("true");
    // The forecast year still shows the overview table, not the first-import page.
    expect(await screen.findByRole("columnheader", { name: "Total" })).toBeInTheDocument();
    expect(screen.queryByText("Your overview starts with an import")).not.toBeInTheDocument();
  });

  it("S and the top-bar switch turn the forecast off and on, remembered, hiding the forecast year", async () => {
    const forecastMonth = (key: string) => ({
      month: key, partial: false, lines: [], actual: { income: "0.00", expense: "0.00" }, income: "0.00", expense: "0.00",
      net: "0.00", closing: "500.00", low: "400.00", high: "600.00", flags: [],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/forecast")) {
        return {
          account_type: "personal", today: "2026-03-10", accounts: [], opening: "500.00", year_ends: [], one_offs: [],
          assumptions: [], backtest: { origins: 0, horizons: [], baseline: {}, recommended: {} },
          months: [forecastMonth("2026-03"), forecastMonth("2027-01")],
        };
      }
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return [];
    });
    localStorage.removeItem("pfm.showForecast");
    await renderApp();
    const toggle = screen.getByRole("switch", { name: "Forecast" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(await screen.findByRole("button", { name: "2027" }));
    press("s");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(localStorage.getItem("pfm.showForecast")).toBe("off");
    // The forecast year disappears, and the selection moves to the latest year with data.
    expect(screen.queryByRole("button", { name: "2027" })).not.toBeInTheDocument();
    await waitFor(() => expect(pressed("2026")).toBe("true"));
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("pfm.showForecast")).toBe("on");
    expect(screen.getByRole("button", { name: "2027" })).toBeInTheDocument();
    localStorage.removeItem("pfm.showForecast");
  });

  it("G toggles the balance graph", async () => {
    await renderApp();
    press("g");
    expect(screen.getByRole("dialog", { name: "Balance history" })).toBeInTheDocument();
    press("g");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("K toggles the shortcuts dialog, Esc closes it, and other keys are ignored while it is open", async () => {
    await renderApp();
    press("k");
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    press("e");
    press("g");
    expect(pressed("Personal")).toBe("true");
    expect(screen.queryByRole("dialog", { name: "Balance history" })).not.toBeInTheDocument();
    press("k");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    press("k");
    press("Escape");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(apiMock).toHaveBeenCalled());
  });

  describe("on the overview", () => {
    const rowOf = (merchant: string) => screen.getByText(merchant).closest("tr") as HTMLElement;
    const loadTransactions = async (items: ReturnType<typeof transaction>[]) => {
      transactionItems = items;
      await renderApp();
      await screen.findByText(items[0].merchant as string);
    };

    it("U opens the editor of the next uncategorized transaction after the active one, wrapping around", async () => {
      await loadTransactions([
        transaction(1, { category_id: null, category: "" }),
        transaction(2),
        transaction(3, { category_id: null, category: "" }),
        transaction(4),
      ]);
      fireEvent.click(rowOf("Merchant 1"));
      press("u");
      expect(rowOf("Merchant 3")).toHaveClass("active-transaction-row");
      expect(within(rowOf("Merchant 3")).getByRole("combobox", { name: "Category" })).toHaveFocus();
      fireEvent.keyDown(screen.getByRole("combobox", { name: "Category" }), { key: "Escape" });
      press("u");
      expect(rowOf("Merchant 1")).toHaveClass("active-transaction-row");
      expect(within(rowOf("Merchant 1")).getByRole("combobox", { name: "Category" })).toBeInTheDocument();
    });

    it("U starts from the top without an active transaction and tells when none is uncategorized", async () => {
      await loadTransactions([transaction(1), transaction(2, { category_id: null, category: "" })]);
      press("u");
      expect(within(rowOf("Merchant 2")).getByRole("combobox", { name: "Category" })).toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole("combobox", { name: "Category" }), { key: "Escape" });
      cleanup();
      await loadTransactions([transaction(1), transaction(2)]);
      press("u");
      expect(screen.getByText("No uncategorized transactions here.")).toBeInTheDocument();
      expect(screen.queryByRole("combobox", { name: "Category" })).not.toBeInTheDocument();
    });

    it("O opens the rule that set the active transaction's category", async () => {
      await loadTransactions([
        transaction(1, { category_source: "rule", category_rule: "market", category_rule_id: 10, category_rule_position: 4 }),
        transaction(2, { category_source: "manual" }),
      ]);
      press("o");
      expect(screen.queryByText("This category wasn't set by a rule.")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Rules" })).toHaveAttribute("aria-pressed", "false");
      fireEvent.click(rowOf("Merchant 1"));
      press("o");
      expect(screen.getByRole("button", { name: "Rules" })).toHaveAttribute("aria-pressed", "true");
    });

    it("O explains when the active transaction's category was not set by a rule", async () => {
      await loadTransactions([transaction(1, { category_source: "manual" })]);
      fireEvent.click(rowOf("Merchant 1"));
      press("o");
      expect(screen.getByText("This category wasn't set by a rule.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Rules" })).toHaveAttribute("aria-pressed", "false");
    });

    it("/ focuses the search without typing into it, and S and N do nothing", async () => {
      await loadTransactions([transaction(1)]);
      press("s");
      expect(screen.getByRole("textbox", { name: "Search transactions" })).not.toHaveFocus();
      const event = new KeyboardEvent("keydown", { key: "/", cancelable: true, bubbles: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(screen.getByRole("textbox", { name: "Search transactions" })).toHaveFocus();
      cleanup();
      await loadTransactions([transaction(1)]);
      press("n");
      expect(screen.queryByRole("textbox", { name: "New category name" })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Text of new rule")).not.toBeInTheDocument();
    });
  });

  describe("bulk categorize from search", () => {
    const rowOf = (merchant: string) => screen.getByText(merchant).closest("tr") as HTMLElement;
    const searchFor = async (text: string) => {
      fireEvent.change(screen.getByRole("textbox", { name: "Search transactions" }), { target: { value: text } });
      await screen.findByRole("checkbox", { name: "Select all listed transactions" });
    };
    const loadAndSearch = async () => {
      transactionItems = [transaction(1), transaction(2), transaction(3), transaction(4, { own_transfer: true })];
      await renderApp();
      await screen.findByText("Merchant 1");
      await searchFor("mer");
    };
    const checkbox = (id: number) => screen.getByRole("checkbox", { name: `Select Merchant ${id} 2026-03-01` });

    it("posts exactly the selected ids with the chosen category, then clears the selection", async () => {
      apiMock.mockImplementation(async (url: string) => {
        if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
        if (url.startsWith("/api/categories")) return [{ id: 1, name: "Groceries" }, { id: 2, name: "Shopping" }, { id: 9, name: "Uncategorized" }];
        if (url === "/api/transactions/bulk-category") return { updated: 2 };
        if (url.startsWith("/api/transactions")) return { total: transactionItems.length, items: transactionItems };
        if (url.startsWith("/api/balance-history")) return { points: [] };
        return [];
      });
      await loadAndSearch();
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
      fireEvent.click(checkbox(1));
      fireEvent.click(checkbox(3));
      expect(screen.getByText("2 selected")).toBeInTheDocument();
      const setCategory = screen.getByRole("button", { name: "Set category" });
      expect(setCategory).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Category for the selected transactions" }));
      fireEvent.click(await screen.findByRole("option", { name: /Shopping/ }));
      expect(setCategory).toBeEnabled();
      fireEvent.click(setCategory);
      await screen.findByText("Set 2 transactions to Shopping.");
      const call = apiMock.mock.calls.find(([url]) => url === "/api/transactions/bulk-category")!;
      expect(call[1]).toEqual({
        method: "POST",
        json: { account_type: "personal", transaction_ids: [1, 3], category_id: 2 },
      });
      await waitFor(() => expect(screen.queryByText("2 selected")).not.toBeInTheDocument());
      expect((checkbox(1) as HTMLInputElement).checked).toBe(false);
    });

    const mockCategories = () => apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
      if (url.startsWith("/api/categories")) return [{ id: 1, name: "Groceries" }, { id: 2, name: "Shopping" }, { id: 9, name: "Uncategorized" }];
      if (url === "/api/transactions/bulk-category") return { updated: 2 };
      if (url.startsWith("/api/transactions/") && url.endsWith("/similar")) {
        return { merchant: "", merchant_count: 0, others: [], suggestion: null };
      }
      if (url.startsWith("/api/transactions")) return { total: transactionItems.length, items: transactionItems };
      if (url.startsWith("/api/balance-history")) return { points: [] };
      return [];
    });

    it("Enter opens the bulk picker, chooses, then applies to exactly the ticked ids", async () => {
      mockCategories();
      await loadAndSearch();
      fireEvent.click(checkbox(1));
      fireEvent.click(checkbox(3));
      press("Enter");
      const picker = await screen.findByRole("combobox", { name: "Category" });
      expect(picker).toHaveFocus();
      expect(screen.queryByRole("option", { name: /Shopping/ })).toBeInTheDocument();
      fireEvent.change(picker, { target: { value: "shop" } });
      fireEvent.keyDown(picker, { key: "Enter" });
      expect(screen.queryByRole("combobox", { name: "Category" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Category for the selected transactions" })).toHaveTextContent("Shopping");
      expect(apiMock.mock.calls.some(([url]) => url === "/api/transactions/bulk-category")).toBe(false);
      press("Enter");
      await screen.findByText("Set 2 transactions to Shopping.");
      const call = apiMock.mock.calls.find(([url]) => url === "/api/transactions/bulk-category")!;
      expect(call[1]).toEqual({
        method: "POST",
        json: { account_type: "personal", transaction_ids: [1, 3], category_id: 2 },
      });
      await waitFor(() => expect(screen.queryByText("2 selected")).not.toBeInTheDocument());
      await waitFor(() => expect(document.activeElement).not.toBe(document.body));
      // Shortcuts still work afterwards.
      press("ArrowDown");
      press("x");
      expect(screen.getByText("1 selected")).toBeInTheDocument();
    });

    it("Escape in the bulk picker closes it and keeps the selection and range", async () => {
      mockCategories();
      await loadAndSearch();
      fireEvent.click(checkbox(1));
      fireEvent.click(checkbox(3));
      press("Enter");
      const picker = await screen.findByRole("combobox", { name: "Category" });
      fireEvent.keyDown(picker, { key: "Escape" });
      expect(screen.queryByRole("combobox", { name: "Category" })).not.toBeInTheDocument();
      expect(screen.getByText("2 selected")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Set category" })).toBeDisabled();
      expect(apiMock.mock.calls.some(([url]) => url === "/api/transactions/bulk-category")).toBe(false);
    });

    it("with nothing ticked, Enter on the active row still opens its category editor", async () => {
      mockCategories();
      await loadAndSearch();
      fireEvent.keyDown(rowOf("Merchant 2"), { key: "Enter" });
      expect(await screen.findByRole("combobox", { name: "Category" })).toBeInTheDocument();
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    });

    it("X toggles the active transaction while searching, and not an uncounted one", async () => {
      await loadAndSearch();
      press("x");
      expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
      fireEvent.click(rowOf("Merchant 2"));
      press("x");
      expect((checkbox(2) as HTMLInputElement).checked).toBe(true);
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      press("x");
      expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
      fireEvent.click(rowOf("Merchant 4"));
      press("x");
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    });

    const loadWithoutSearch = async () => {
      transactionItems = [transaction(1), transaction(2), transaction(3), transaction(4, { own_transfer: true })];
      await renderApp();
      await screen.findByText("Merchant 1");
    };

    it("without a search, X shows the column and bar on the first selection and hides them when it empties", async () => {
      await loadWithoutSearch();
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
      fireEvent.click(rowOf("Merchant 1"));
      press("x");
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: "Select all listed transactions" })).toBeInTheDocument();
      expect((checkbox(1) as HTMLInputElement).checked).toBe(true);
      press("x");
      expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
    });

    it("without a search, X skips rows that cannot be ticked", async () => {
      await loadWithoutSearch();
      fireEvent.click(rowOf("Merchant 4"));
      press("x");
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
    });

    it("without a search, Clear and unticking the last row hide the column", async () => {
      await loadWithoutSearch();
      fireEvent.click(rowOf("Merchant 1"));
      press("x");
      fireEvent.click(screen.getByRole("button", { name: "Clear" }));
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
      press("x");
      fireEvent.click(checkbox(1));
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
    });

    it("without a search, select-all picks every selectable listed row", async () => {
      await loadWithoutSearch();
      fireEvent.click(rowOf("Merchant 1"));
      press("x");
      fireEvent.click(screen.getByRole("checkbox", { name: "Select all listed transactions" }));
      expect(screen.getByText("3 selected")).toBeInTheDocument();
    });

    it("changing the category filter clears the selection", async () => {
      apiMock.mockImplementation(async (url: string) => {
        if (url.startsWith("/api/report")) {
          return {
            months: ["2026-03"],
            summaries: [{
              ...summary,
              categories: [{ category_id: 1, flow_type: "expense", name: "Groceries", amount: "-40.00", transaction_count: 3 }],
            }],
          };
        }
        if (url.startsWith("/api/transactions")) return { total: transactionItems.length, items: transactionItems };
        if (url.startsWith("/api/balance-history")) return { points: [] };
        return [];
      });
      await loadWithoutSearch();
      fireEvent.click(rowOf("Merchant 1"));
      press("x");
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("row", { name: "Filter transactions by Groceries expense" }));
      await waitFor(() => expect(screen.queryByText("1 selected")).not.toBeInTheDocument());
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
    });

    it("select-all picks the selectable rows, and changing the search clears the selection", async () => {
      await loadAndSearch();
      fireEvent.click(screen.getByRole("checkbox", { name: "Select all listed transactions" }));
      expect(screen.getByText("3 selected")).toBeInTheDocument();
      fireEvent.change(screen.getByRole("textbox", { name: "Search transactions" }), { target: { value: "merc" } });
      await waitFor(() => expect(screen.queryByText("3 selected")).not.toBeInTheDocument());
    });
  });

  describe("Shift+arrow ranges", () => {
    const rowOf = (merchant: string) => screen.getByText(merchant).closest("tr") as HTMLElement;
    const checkbox = (id: number) => screen.getByRole("checkbox", { name: `Select Merchant ${id} 2026-03-01` }) as HTMLInputElement;
    const shiftDown = () => fireEvent.keyDown(window, { key: "ArrowDown", shiftKey: true });
    const inRange = () => [...document.querySelectorAll("tr.in-range")].map((row) => row.textContent?.match(/Merchant \d/)?.[0]);
    const load = async (items: ReturnType<typeof transaction>[], search = "mer") => {
      transactionItems = items;
      await renderApp();
      await screen.findByText("Merchant 1");
      if (search) {
        fireEvent.change(screen.getByRole("textbox", { name: "Search transactions" }), { target: { value: search } });
        await screen.findByRole("checkbox", { name: "Select all listed transactions" });
      }
      fireEvent.click(rowOf("Merchant 1"));
    };
    const plain = () => [transaction(1), transaction(2), transaction(3), transaction(4)];

    it("highlights the rows from the anchor to the active row", async () => {
      await load(plain());
      shiftDown();
      shiftDown();
      expect(inRange()).toEqual(["Merchant 1", "Merchant 2", "Merchant 3"]);
      fireEvent.keyDown(window, { key: "ArrowUp", shiftKey: true });
      expect(inRange()).toEqual(["Merchant 1", "Merchant 2"]);
    });

    it("X checks the whole range, then unchecks it, keeping the range", async () => {
      await load(plain());
      shiftDown();
      shiftDown();
      press("x");
      expect([1, 2, 3, 4].map((id) => checkbox(id).checked)).toEqual([true, true, true, false]);
      expect(inRange()).toHaveLength(3);
      press("x");
      expect([1, 2, 3, 4].map((id) => checkbox(id).checked)).toEqual([false, false, false, false]);
    });

    it("X checks a mixed range fully", async () => {
      await load(plain());
      fireEvent.click(checkbox(2));
      shiftDown();
      shiftDown();
      press("x");
      expect([1, 2, 3].map((id) => checkbox(id).checked)).toEqual([true, true, true]);
    });

    it("skips rows that cannot be checked", async () => {
      await load([transaction(1), transaction(2, { own_transfer: true }), transaction(3), transaction(4)]);
      shiftDown();
      shiftDown();
      expect(inRange()).toHaveLength(3);
      press("x");
      expect(checkbox(1).checked).toBe(true);
      expect(checkbox(3).checked).toBe(true);
      expect(screen.getByText("2 selected")).toBeInTheDocument();
    });

    it("a plain arrow ends the range and X then toggles only the active row", async () => {
      await load(plain());
      shiftDown();
      shiftDown();
      press("ArrowDown");
      expect(inRange()).toEqual([]);
      press("x");
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      expect(checkbox(4).checked).toBe(true);
    });

    it("clicking a row ends the range", async () => {
      await load(plain());
      shiftDown();
      expect(inRange()).toHaveLength(2);
      fireEvent.click(rowOf("Merchant 4"));
      expect(inRange()).toEqual([]);
    });

    it("changing the search ends the range", async () => {
      await load(plain());
      shiftDown();
      fireEvent.change(screen.getByRole("textbox", { name: "Search transactions" }), { target: { value: "merc" } });
      await waitFor(() => expect(inRange()).toEqual([]));
    });

    it("Escape clears the range first, then unselects the active row", async () => {
      await load(plain());
      shiftDown();
      shiftDown();
      press("Escape");
      expect(inRange()).toEqual([]);
      expect(document.querySelector("tr.active-transaction-row")).not.toBeNull();
      press("Escape");
      expect(document.querySelector("tr.active-transaction-row")).toBeNull();
    });

    it("Shift+arrow ranges and X work without a search", async () => {
      await load(plain(), "");
      shiftDown();
      shiftDown();
      expect(inRange()).toEqual(["Merchant 1", "Merchant 2", "Merchant 3"]);
      press("x");
      expect([1, 2, 3, 4].map((id) => checkbox(id).checked)).toEqual([true, true, true, false]);
      expect(screen.getByText("3 selected")).toBeInTheDocument();
      press("x");
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
      expect(inRange()).toHaveLength(3);
    });

    it("Escape deselects all ticked transactions and ends the range in one press", async () => {
      await load(plain(), "");
      shiftDown();
      press("x");
      press("Escape");
      expect(inRange()).toEqual([]);
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /^Select/ })).not.toBeInTheDocument();
      expect(document.querySelector("tr.active-transaction-row")).not.toBeNull();
    });
  });

  describe("Accounts and import shortcuts", () => {
    it("I opens the Accounts view and the file chooser at once, and again on a second press", async () => {
      await renderApp();
      const click = vi.spyOn(HTMLInputElement.prototype, "click");
      press("i");
      await screen.findByRole("heading", { name: /Accounts and cards/ });
      expect(click).toHaveBeenCalledTimes(1);
      expect(click.mock.contexts[0]).toHaveProperty("type", "file");
      press("i");
      expect(screen.getByRole("heading", { name: /Accounts and cards/ })).toBeInTheDocument();
      expect(click).toHaveBeenCalledTimes(2);
      click.mockRestore();
    });

    it("A toggles the Accounts view, and the top bar has no Imports button", async () => {
      await renderApp();
      expect(screen.queryByRole("button", { name: "Imports" })).not.toBeInTheDocument();
      press("a");
      expect(await screen.findByRole("heading", { name: /Accounts and cards/ })).toBeInTheDocument();
      expect(pressed("Accounts")).toBe("true");
      press("a");
      expect(screen.queryByRole("heading", { name: /Accounts and cards/ })).not.toBeInTheDocument();
    });
  });

  describe("N in the full-page views", () => {
    const newEvent = () => {
      const event = new KeyboardEvent("keydown", { key: "n", cancelable: true, bubbles: true });
      act(() => {
        window.dispatchEvent(event);
      });
      return event;
    };

    it("focuses the new category name in Categories", async () => {
      await renderApp();
      press("c");
      await screen.findByLabelText("New category name");
      expect(newEvent().defaultPrevented).toBe(true);
      expect(screen.getByLabelText("New category name")).toHaveFocus();
    });

    it("focuses the backup note after a click in Backups", async () => {
      await renderApp();
      press("a");
      await screen.findByLabelText("Backup note");
      fireEvent.pointerDown(screen.getByRole("region", { name: "Backups" }));
      newEvent();
      expect(screen.getByLabelText("Backup note")).toHaveFocus();
    });

    it("opens the file chooser in Accounts", async () => {
      await renderApp();
      const click = vi.spyOn(HTMLInputElement.prototype, "click");
      press("a");
      await screen.findByRole("heading", { name: /Accounts and cards/ });
      expect(click).not.toHaveBeenCalled();
      newEvent();
      expect(click).toHaveBeenCalledTimes(1);
      expect(click.mock.contexts[0]).toHaveProperty("type", "file");
      click.mockRestore();
    });

    it("starts a new rule in Rules", async () => {
      apiMock.mockImplementation(async (url: string) => {
        if (url.startsWith("/api/report")) return { months: ["2026-03"], summaries: [summary] };
        if (url.startsWith("/api/category-management")) {
          return [{ id: 1, name: "Groceries", transaction_count: 0, rules: [] }];
        }
        if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
        return [];
      });
      await renderApp();
      press("r");
      await screen.findByRole("button", { name: "New rule" });
      await waitFor(() => expect(screen.getByRole("button", { name: "New rule" })).not.toBeDisabled());
      newEvent();
      expect(await screen.findByLabelText("Text of new rule")).toBeInTheDocument();
    });
  });
});

describe("Follow-ups", () => {
  const annotationCalls = () => apiMock.mock.calls.filter(([url]) => String(url).endsWith("/annotation"));
  const transactionUrls = () => apiMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith("/api/transactions?"));
  const rowOf = (merchant: string) => screen.getByText(merchant).closest("tr") as HTMLElement;
  const activate = async (merchant: string) => {
    await screen.findByText(merchant);
    const row = rowOf(merchant);
    fireEvent.focus(row);
    return row;
  };

  beforeEach(() => {
    transactionItems = [transaction(1), transaction(2, { follow_up: "open", follow_up_at: "2026-03-02T09:00:00", note: "ask" })];
    const base = apiMock.getMockImplementation()!;
    apiMock.mockImplementation(async (url: string, init?: { json?: Record<string, unknown> }) => {
      if (url.endsWith("/annotation")) {
        return { note: "", follow_up: null, ...init?.json, follow_up_at: init?.json?.follow_up ? "2026-03-09T09:00:00" : null };
      }
      if (url.startsWith("/api/transactions?")) {
        return { total: transactionItems.length, items: transactionItems, open_follow_ups: 1 };
      }
      return base(url);
    });
  });

  it("Shift+M toggles between All and Follow-ups, which fetches follow_up across years and clears the category filter", async () => {
    await renderApp();
    await screen.findByText("Merchant 1");
    expect(screen.getByRole("button", { name: "Follow-ups 1" })).toHaveAttribute("aria-pressed", "false");
    pressShiftM();
    await waitFor(() => expect(transactionUrls().some((url) => url.includes("follow_up=open"))).toBe(true));
    expect(screen.getByRole("button", { name: "Follow-ups 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: /all years/ })).toBeInTheDocument();
    pressShiftM();
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });

  it("the Done filter fetches done follow-ups", async () => {
    await renderApp();
    fireEvent.click(within(screen.getByRole("group", { name: "Show transactions" })).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(transactionUrls().some((url) => url.includes("follow_up=done"))).toBe(true));
  });

  it("M flags the active transaction, focuses its note, and M again removes the flag", async () => {
    await renderApp();
    await activate("Merchant 1");
    press("m");
    await waitFor(() => expect(annotationCalls()).toHaveLength(1));
    expect(annotationCalls()[0][0]).toBe("/api/transactions/1/annotation");
    expect(annotationCalls()[0][1]).toMatchObject({ method: "PATCH", json: { follow_up: "open" } });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Note" })).toHaveFocus());
    expect(screen.getByRole("checkbox", { name: "Follow up" })).toBeChecked();
    // Back on the row, M removes the flag.
    fireEvent.focus(rowOf("Merchant 1"));
    (document.activeElement as HTMLElement).blur();
    press("m");
    await waitFor(() => expect(annotationCalls()).toHaveLength(2));
    expect(annotationCalls()[1][1]).toMatchObject({ json: { follow_up: null } });
  });

  it("D marks an open follow-up done, reopens a done one, and says so when there is none", async () => {
    await renderApp();
    await activate("Merchant 1");
    press("d");
    await screen.findByText("This transaction has no follow-up.");
    expect(annotationCalls()).toHaveLength(0);
    await activate("Merchant 2");
    press("d");
    await waitFor(() => expect(annotationCalls()).toHaveLength(1));
    expect(annotationCalls()[0][1]).toMatchObject({ json: { follow_up: "done" } });
    await screen.findByText(/Done on 2026-03-09/);
    press("d");
    await waitFor(() => expect(annotationCalls()).toHaveLength(2));
    expect(annotationCalls()[1][1]).toMatchObject({ json: { follow_up: "open" } });
  });

  it("F, M and D are ignored while typing in a field", async () => {
    await renderApp();
    await activate("Merchant 1");
    const search = screen.getByRole("textbox", { name: "Search transactions" });
    for (const key of ["f", "m", "d"]) fireEvent.keyDown(search, { key });
    expect(annotationCalls()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    const note = await screen.findByRole("textbox", { name: "Note" });
    for (const key of ["f", "m", "d"]) fireEvent.keyDown(note, { key });
    expect(annotationCalls()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps a row in the Follow-ups list, shown done, after it is marked done", async () => {
    await renderApp();
    pressShiftM();
    await activate("Merchant 2");
    fireEvent.click(within(screen.getByRole("group", { name: "Selected transaction" })).getByRole("button", { name: "Done" }));
    await screen.findByText(/Done on 2026-03-09/);
    expect(screen.getByText("Merchant 2")).toBeInTheDocument();
  });

  it("Follow-ups clears a category filter, and picking a category switches back to All", async () => {
    const base = apiMock.getMockImplementation()!;
    apiMock.mockImplementation(async (url: string, init?: unknown) => {
      if (url.startsWith("/api/report")) {
        return { months: ["2026-03"], summaries: [{ ...summary, categories: [
          { category_id: 1, flow_type: "expense", name: "Groceries", amount: "-40.00", transaction_count: 3 },
        ] }] };
      }
      return base(url, init);
    });
    await renderApp();
    const category = () => screen.findByRole("row", { name: "Filter transactions by Groceries expense" });
    fireEvent.click(await category());
    await waitFor(() => expect(transactionUrls().some((url) => url.includes("category_id=1"))).toBe(true));
    const filter = screen.getByRole("group", { name: "Show transactions" });
    fireEvent.click(within(filter).getByRole("button", { name: /^Follow-ups/ }));
    await waitFor(() => expect(transactionUrls().at(-1)).toContain("follow_up=open"));
    expect(transactionUrls().at(-1)).not.toContain("category_id");
    expect(screen.queryByRole("button", { name: /Groceries · expense/ })).not.toBeInTheDocument();
    fireEvent.click(await category());
    expect(within(filter).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(transactionUrls().at(-1)).toContain("category_id=1"));
    expect(transactionUrls().at(-1)).not.toContain("follow_up");
  });
});
