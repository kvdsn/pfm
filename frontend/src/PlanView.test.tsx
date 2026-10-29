import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlanView } from "./PlanView";
import type { AccountType, Category, ConsultingCheck, CorporateTax, DayOff, DayRate, Forecast, ForecastSettings, PlannedItem, SalarySchedule } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({
  api: apiMock,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

const categories: Category[] = [
  { id: 1, name: "Uncategorized", rule_flow_type: null },
  { id: 41, name: "Consulting income", rule_flow_type: null, is_income: true },
  { id: 42, name: "Corporate tax", rule_flow_type: null },
  { id: 43, name: "VAT", rule_flow_type: null },
  { id: 44, name: "Housing", rule_flow_type: null },
  { id: 45, name: "Travel & meals", rule_flow_type: null },
];
const settings: ForecastSettings = {
  personal_min_balance: "2000.00", business_buffer: "2000.00", payment_delay_months: 1, vat_rate: 21, vat_delay_months: 1,
  vat_day: 15, baseline_months: 6, baseline_weighted: false, one_off_factor: 3, salary_net_percent: 0, reimbursement_delay_days: 30,
  tax_reduced_threshold: "51000.00", tax_reduced_rate: 20, tax_reduced_band: "100000.00", tax_standard_rate: 25,
  tax_base_rate: 3, tax_surcharge_applies: true, tax_profit_excluded: [], loan_start: "2026-01-01", loan_interest_rate: 3.5,
  roles: {
    consulting_income: 41, vat: 43, corporate_tax: 42, salary_business: null, salary_personal: null,
    transfer_business: null, transfer_personal: null, loan_interest_business: null, loan_interest_personal: null,
  },
  closed_categories: { personal: [], business: [44], kids: [] },
};
const forecast = {
  account_type: "business", today: "2026-10-25",
  accounts: [{ name: "KBC Business", balance: "9000.00", as_of: "2026-10-24" }],
  opening: "9000.00",
  months: [
    { month: "2026-11", flags: ["transfer_short"], lines: [] },
    { month: "2026-12", flags: [], lines: [] },
  ],
  year_ends: [{ year: "2026", closing: "30000.00", low: "29500.00", high: "31800.00" }],
  one_offs: [{ transaction_id: 7, date: "2026-03-02", merchant: "Tax office", category: "Corporate tax", amount: "-8000.00" }],
  assumptions: ["Personal receives 62.5% of the salary, as over the last 12 months."],
  backtest: {
    origins: 9, horizons: [{ months: 1, p10: "-120.00", p90: "200.00", mae: "90.00" }],
    baseline: { baseline_months: 6, baseline_weighted: false }, recommended: { baseline_months: 12, baseline_weighted: true },
  },
} as unknown as Forecast;

const planned = (id: number, overrides: Partial<PlannedItem> = {}): PlannedItem => ({
  id, account_type: "business", date: "2026-12-01", amount: "-4500.00", category_id: 42, category: "Corporate tax",
  note: "Tax", linked: null, ...overrides,
});
let plannedItems: PlannedItem[];
let otherPlanned: PlannedItem[];
let salary: SalarySchedule[];
let rates: DayRate[];
let daysOff: DayOff[];
let consulting: ConsultingCheck;
let failure: string | null;
let tax: CorporateTax;

beforeEach(() => {
  apiMock.mockReset();
  plannedItems = [planned(5)];
  otherPlanned = [planned(9, { account_type: "personal", date: "2026-12-02", amount: "3000.00", category_id: null, category: null, note: "Dividend" })];
  salary = [{ year: "2026", monthly_amount: "4000.00", last_month: 10, minimum_amount: "0.00" }];
  rates = [{ effective_date: "2026-01-01", rate: "800.00" }];
  daysOff = [{ id: 3, start_date: "2026-12-24", end_date: "2026-12-31", fraction: 1, note: "Christmas", repeat_weeks: 0, repeat_until: null },
    { id: 4, start_date: "2026-01-14", end_date: "2026-01-14", fraction: 0.5, note: "Wednesday", repeat_weeks: 2, repeat_until: null }];
  consulting = {
    months: [
      {
        month: "2026-07", working_days: 22, days_off: 0, days_expected: 22, expected: "21296.00", due: "2026-08-31",
        received: [], days_paid: 0, status: "late", vat: null,
      },
      {
        month: "2026-08", working_days: 21, days_off: 0, days_expected: 21, expected: "20328.00", due: "2026-09-30",
        received: [{ date: "2026-09-30", amount: "18392.00", transaction_id: 5 }], days_paid: 19, status: "differs",
        vat: { expected: "3192.00", due: "2026-10-15", paid: [], status: "missing" },
      },
    ],
    summary: { late: 1, differs: 1, fewer_days: 0, vat_missing: 1 },
  };
  tax = {
    years: [{
      year: "2026", profit: "90000.00", eligible: true, salary: "52000.00", tax: "18000.00",
      breakdown: [{ category: "Consulting income", amount: "100000.00" }, { category: "Tools", amount: "-10000.00" }],
      deadlines: [
        { date: "2026-04-10", prepaid: "1500.00", planned: "0.00", advised: "0.00", past: true },
        { date: "2026-07-10", prepaid: "200.00", planned: "0.00", advised: "0.00", past: true },
        { date: "2026-10-10", prepaid: "0.00", planned: "0.00", advised: "0.00", past: true },
        { date: "2026-12-20", prepaid: "400.00", planned: "4500.00", advised: "23267.00", past: false },
      ],
      prepaid_total: "2100.00", planned_total: "4500.00", surcharge_as_planned: "1015.50", closed: false,
      assumptions: ["All corporate tax paid in 2026 counts as a prepayment for 2026."],
    }, {
      year: "2027", profit: "0.00", eligible: false, salary: "0.00", tax: "0.00", breakdown: [],
      deadlines: [], prepaid_total: "0.00", planned_total: "0.00", surcharge_as_planned: "0.00", closed: false, assumptions: [],
    }],
  };
  failure = null;
  apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
    if (init?.method && init.method !== "GET" && failure) throw new Error(failure);
    if (url === "/api/forecast/settings") return settings;
    if (url === "/api/forecast/salary") return salary;
    if (url === "/api/forecast/day-rates") return rates;
    if (url === "/api/forecast/days-off") return daysOff;
    if (url === "/api/forecast/consulting") return consulting;
    if (url === "/api/forecast/corporate-tax") return tax;
    if (url === "/api/forecast/director-loan") return { start: "2026-01-01", rate: 3.5, balance: "0.00", interest_to_date: "0.00", interest_paid: "0.00", years: [], movements: [], payments: [], notes: [] };
    if (url === "/api/planned?account_type=business") return plannedItems;
    if (url.startsWith("/api/planned?account_type=")) return url.endsWith("personal") ? otherPlanned : [];
    return {};
  });
});

afterEach(cleanup);

const renderView = async (accountType: AccountType = "business") => {
  const props = { onChanged: vi.fn(), onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn() };
  render(<PlanView accountType={accountType} categories={categories} forecast={forecast} {...props} />);
  await screen.findByRole("region", { name: "Baselines" });
  if (accountType === "business") await screen.findByRole("table", { name: "Planned items" });
  return props;
};
const calls = (method: string) => apiMock.mock.calls.filter(([, init]) => init?.method === method);
const section = (name: string) => within(screen.getByRole("region", { name }));

describe("PlanView outlook", () => {
  it("lists the year ends, assumptions, short months and balance dates", async () => {
    await renderView();
    const outlook = section("Outlook");
    expect(outlook.getByText(/End of 2026/)).toHaveTextContent("€ 30.000,00");
    expect(outlook.getByText(/End of 2026/)).toHaveTextContent("€ 29.500,00 – € 31.800,00");
    expect(outlook.getByText(/Personal receives 62.5%/)).toBeInTheDocument();
    expect(outlook.getByText(/Months with a transfer short/).parentElement).toHaveTextContent("Nov 2026");
    expect(outlook.getByText(/Balances as of/).parentElement).toHaveTextContent("KBC Business 2026-10-24");
  });

  it("shows the sections of an entity, and none that do not apply to Kids", async () => {
    await renderView();
    for (const name of ["Salary", "Consulting", "Balances", "Baselines", "Categories used by the models"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    cleanup();
    await renderView("kids");
    for (const name of ["Salary", "Consulting", "Balances", "Categories used by the models"]) {
      expect(screen.queryByRole("region", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("region", { name: "Baselines" })).toBeInTheDocument();
  });
});

describe("PlanView consulting payments", () => {
  it("lists the months newest first with their status tags and a summary", async () => {
    await renderView();
    const consultingSection = section("Consulting");
    expect(consultingSection.getByText("1 late, 1 differs, 1 VAT missing")).toBeInTheDocument();
    const rows = within(consultingSection.getByRole("table", { name: "Consulting payments" })).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Aug 2026");
    expect(rows[0]).toHaveTextContent("2026-09-30");
    expect(rows[1]).toHaveTextContent("Jul 2026");
    expect(within(rows[0]).getByText("Differs")).toHaveAttribute("title", expect.stringContaining("2 days fewer than expected, but not a whole or half number of days at the rate"));
    expect(within(rows[0]).getByText("VAT missing")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Late")).toBeInTheDocument();
  });

  it("says all is well when nothing needs attention", async () => {
    consulting = { months: [], summary: { late: 0, differs: 0, fewer_days: 0, vat_missing: 0 } };
    await renderView();
    expect(section("Consulting").getByText("All payments as expected")).toBeInTheDocument();
    expect(section("Outlook").queryByText(/consulting payment/)).not.toBeInTheDocument();
  });

  it("warns in the outlook about late payments and missing VAT that fell due recently", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-26T12:00:00"));
    try {
      await renderView();
      expect(await section("Outlook").findByText("1 consulting payment late (Jul 2026)")).toBeInTheDocument();
      expect(section("Outlook").getByText("1 VAT payment missing (Aug 2026), or not imported yet")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaves older problems to the payments table", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2027-02-01T12:00:00"));  // both fell due more than 60 days before
    try {
      await renderView();
      await section("Consulting").findByText("Payments");
      expect(section("Outlook").queryByText(/consulting payment late/)).not.toBeInTheDocument();
      expect(section("Outlook").queryByText(/VAT payment missing/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("PlanView planned items", () => {
  it("adds an item and links it to another entity's", async () => {
    const props = await renderView();
    fireEvent.click(screen.getByRole("button", { name: "Add planned item" }));
    const form = within(screen.getByRole("form", { name: "Add planned item" }));
    fireEvent.change(form.getByLabelText("Date"), { target: { value: "2027-02-01" } });
    fireEvent.change(form.getByLabelText("Amount"), { target: { value: "-1.200,50" } });
    fireEvent.change(form.getByLabelText("Category"), { target: { value: "42" } });
    fireEvent.change(form.getByLabelText("Note"), { target: { value: "Insurance" } });
    fireEvent.change(form.getByLabelText("Linked to"), { target: { value: "9" } });
    apiMock.mockImplementationOnce(async () => planned(6));
    fireEvent.click(form.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/planned");
    expect(calls("POST")[0][1].json).toEqual({
      account_type: "business", date: "2027-02-01", amount: "-1.200,50", category_id: 42, note: "Insurance",
    });
    expect(calls("PUT")[0][0]).toBe("/api/planned/6/link");
    expect(calls("PUT")[0][1].json).toEqual({ account_type: "business", linked_item_id: 9 });
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it("edits an item inline and unlinks it", async () => {
    plannedItems = [planned(5, { linked: { id: 9, account_type: "personal", date: "2026-12-02", amount: "3000.00", note: "Dividend" } })];
    await renderView();
    fireEvent.click(screen.getByRole("button", { name: "Edit planned item 2026-12-01" }));
    const form = within(screen.getByRole("form", { name: "Edit planned item" }));
    expect(form.getByLabelText("Linked to")).toHaveValue("9");
    fireEvent.change(form.getByLabelText("Amount"), { target: { value: "-5000" } });
    fireEvent.change(form.getByLabelText("Linked to"), { target: { value: "" } });
    fireEvent.click(form.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0][0]).toBe("/api/planned/5");
    expect(calls("PATCH")[0][1].json).toMatchObject({ account_type: "business", amount: "-5000", category_id: 42 });
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ account_type: "business", linked_item_id: null });
  });

  it("deletes an item", async () => {
    const props = await renderView();
    fireEvent.click(screen.getByRole("button", { name: "Delete planned item 2026-12-01" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/api/planned/5?account_type=business");
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it("shows the server's message inside the form", async () => {
    await renderView();
    fireEvent.click(screen.getByRole("button", { name: "Add planned item" }));
    failure = "Invalid date: ";
    const form = within(screen.getByRole("form", { name: "Add planned item" }));
    fireEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByRole("alert")).toHaveTextContent("Invalid date");
  });

  it("opens the form when the new-item signal rises", async () => {
    const props = { onChanged: vi.fn(), onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn() };
    const view = (signal: number) => (
      <PlanView accountType="business" categories={categories} forecast={forecast} newItemSignal={signal} {...props} />
    );
    const { rerender } = render(view(0));
    await screen.findByRole("region", { name: "Planned items" });
    expect(screen.queryByRole("form", { name: "Add planned item" })).not.toBeInTheDocument();
    rerender(view(1));
    expect(screen.getByRole("form", { name: "Add planned item" })).toBeInTheDocument();
  });
});

describe("PlanView salary, consulting and settings", () => {
  it("saves and deletes a salary schedule by year", async () => {
    await renderView();
    fireEvent.change(screen.getByLabelText("Monthly salary 2026"), { target: { value: "4200" } });
    fireEvent.change(screen.getByLabelText("Last month 2026"), { target: { value: "11" } });
    // A saved minimum of 0 shows as empty; one typed in is sent as written.
    expect(screen.getByLabelText("Minimum after 2026's last month")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Minimum after 2026's last month"), { target: { value: "250,00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save salary 2026" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][0]).toBe("/api/forecast/salary/2026");
    expect(calls("PUT")[0][1].json).toEqual({ monthly_amount: "4200", last_month: 11, minimum_amount: "250,00" });
    fireEvent.click(screen.getByRole("button", { name: "Delete salary 2026" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/api/forecast/salary/2026");
  });

  it("adds a salary year and shows the derived net share", async () => {
    await renderView();
    fireEvent.change(screen.getByLabelText("New salary year"), { target: { value: "2027" } });
    fireEvent.change(screen.getByLabelText("New monthly salary"), { target: { value: "4300" } });
    fireEvent.click(screen.getByRole("button", { name: "Add salary year" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][0]).toBe("/api/forecast/salary/2027");
    expect(calls("PUT")[0][1].json).toEqual({ monthly_amount: "4300", last_month: 12, minimum_amount: null });
    expect(section("Salary").getByText(/currently/)).toHaveTextContent("62.5%");
  });

  it("adds and deletes a day rate and a day off", async () => {
    await renderView();
    fireEvent.change(screen.getByLabelText("New day rate date"), { target: { value: "2027-01-01" } });
    fireEvent.change(screen.getByLabelText("New day rate"), { target: { value: "850" } });
    fireEvent.click(screen.getByRole("button", { name: "Add day rate" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][0]).toBe("/api/forecast/day-rates/2027-01-01");
    expect(calls("PUT")[0][1].json).toEqual({ rate: "850" });
    fireEvent.click(screen.getByRole("button", { name: "Delete day rate 2026-01-01" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/api/forecast/day-rates/2026-01-01");

    fireEvent.change(screen.getByLabelText("New day off start"), { target: { value: "2027-04-01" } });
    fireEvent.change(screen.getByLabelText("New day off end"), { target: { value: "2027-04-01" } });
    fireEvent.click(screen.getByLabelText("New day off is half days"));
    expect(screen.getByRole("cell", { name: "Every 2 weeks" })).toBeTruthy();
    expect(screen.queryByLabelText("New day off repeats until")).toBeNull();
    fireEvent.change(screen.getByLabelText("New day off repeats"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("New day off repeats until"), { target: { value: "2027-06-30" } });
    fireEvent.change(screen.getByLabelText("New day off note"), { target: { value: "Dentist" } });
    fireEvent.click(screen.getByRole("button", { name: "Add days off" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/forecast/days-off");
    expect(calls("POST")[0][1].json).toEqual({ start_date: "2027-04-01", end_date: "2027-04-01", fraction: 0.5, note: "Dentist", repeat_weeks: 4, repeat_until: "2027-06-30" });
    fireEvent.click(screen.getByRole("button", { name: "Delete days off 2026-12-24" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(2));
    expect(calls("DELETE")[1][0]).toBe("/api/forecast/days-off/3");
  });

  it("saves only the settings that changed, and tells App to reload the forecast", async () => {
    const props = await renderView();
    const consulting = section("Consulting");
    expect(consulting.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(consulting.getByLabelText("VAT rate (%)"), { target: { value: "6" } });
    fireEvent.click(consulting.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][0]).toBe("/api/forecast/settings");
    expect(calls("PUT")[0][1].json).toEqual({ vat_rate: 6 });
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it("shows the server's message under the section's Save button", async () => {
    await renderView();
    const balances = section("Balances");
    fireEvent.change(balances.getByLabelText(/^Business buffer/), { target: { value: "abc" } });
    failure = "Invalid business buffer: abc";
    fireEvent.click(balances.getByRole("button", { name: "Save" }));
    expect(await balances.findByRole("alert")).toHaveTextContent("Invalid business buffer: abc");
    expect(calls("PUT")[0][1].json).toEqual({ business_buffer: "abc" });
  });

  it("saves the baseline settings and the closed categories of this entity", async () => {
    await renderView();
    const baselines = section("Baselines");
    // The models' own categories never get a baseline, so they are not offered.
    expect(baselines.queryByLabelText("VAT")).not.toBeInTheDocument();
    expect(baselines.getByLabelText("Housing")).toBeChecked();
    fireEvent.click(baselines.getByLabelText("Housing"));
    fireEvent.click(baselines.getByLabelText("Travel & meals"));
    fireEvent.click(baselines.getByLabelText(/Weighted/));
    fireEvent.click(baselines.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ baseline_weighted: true, closed_categories: { business: [45] } });
  });

  it("lists the one-offs and the backtest", async () => {
    await renderView();
    const baselines = section("Baselines");
    expect(baselines.getByRole("table", { name: "One-offs" })).toHaveTextContent("Tax office");
    expect(baselines.getByLabelText("Backtest")).toHaveTextContent("9 origins; recommended 12 months, weighted; p10 to p90 1 mo −€120 to €200");
  });

  it("keeps the category roles collapsed until asked, then saves a changed role", async () => {
    await renderView();
    expect(screen.queryByLabelText("VAT", { selector: "select" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /categories used by the models/ }));
    const roles = section("Categories used by the models");
    fireEvent.change(roles.getByLabelText("Corporate tax"), { target: { value: "" } });
    fireEvent.click(roles.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ roles: { corporate_tax: null } });
  });
});

describe("PlanView corporate tax", () => {
  it("shows the estimate, the deadlines and the surcharge, with the breakdown collapsed", async () => {
    await renderView();
    const tax = section("Corporate tax");
    const year = within(tax.getByRole("group", { name: "Corporate tax 2026" }));
    expect(year.getByText(/Reduced rate: salary € 52\.000,00 ≥ € 51\.000,00/)).toBeInTheDocument();
    expect(year.getByText(/Estimated tax/).parentElement).toHaveTextContent("€ 18.000,00");
    expect(year.queryByRole("table", { name: "Profit breakdown 2026" })).not.toBeInTheDocument();
    fireEvent.click(year.getByRole("button", { name: /Show the profit breakdown/ }));
    expect(year.getByRole("table", { name: "Profit breakdown 2026" })).toHaveTextContent("Consulting income");
    const rows = within(year.getByRole("table", { name: "Prepayments 2026" })).getAllByRole("row");
    expect(rows[1]).toHaveClass("tax-past");
    expect(rows[4]).toHaveTextContent("2026-12-20");
    expect(rows[4]).toHaveTextContent("€ 23.267,00");
    expect(year.getByText(/Surcharge as planned/)).toHaveClass("plan-warning");
    expect(tax.getByRole("group", { name: "Corporate tax 2027" })).toBeInTheDocument();
  });

  it("is not shown for Personal", async () => {
    await renderView("personal");
    expect(screen.queryByRole("region", { name: "Corporate tax" })).not.toBeInTheDocument();
  });

  it("saves the tax rules", async () => {
    await renderView();
    const rules = section("Corporate tax");
    fireEvent.change(rules.getByLabelText(/Base rate for prepayments/), { target: { value: "3.5" } });
    fireEvent.click(rules.getByLabelText("The surcharge applies"));
    fireEvent.click(rules.getByLabelText("Housing"));
    fireEvent.click(rules.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ tax_base_rate: 3.5, tax_surcharge_applies: false, tax_profit_excluded: [44] });
  });

  it("asks before replacing the planned items, then applies the advice", async () => {
    await renderView();
    const tax = section("Corporate tax");
    fireEvent.click(tax.getByRole("button", { name: "Use advice for 2026" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText(/2026-12-01 · -€ 4\.500,00 · Tax/)).toBeInTheDocument();
    expect(dialog.getByText(/2026-12-20 · -€ 23\.267,00/)).toBeInTheDocument();
    expect(calls("POST")).toHaveLength(0);
    fireEvent.click(dialog.getByRole("button", { name: "Replace planned items" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/forecast/corporate-tax/2026/apply");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("cancels without applying", async () => {
    await renderView();
    fireEvent.click(section("Corporate tax").getByRole("button", { name: "Use advice for 2026" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls("POST")).toHaveLength(0);
  });

  it("shows the director loan and saves only the changed loan rules", async () => {
    await renderView();
    const loan = section("Director loan");
    expect(loan.getByText("Loan rules")).toBeInTheDocument();
    fireEvent.change(loan.getByLabelText(/Interest rate/), { target: { value: "4" } });
    fireEvent.click(loan.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ loan_interest_rate: 4 });
  });

  it("shows the director loan and saves only the changed loan rules", async () => {
    await renderView();
    const loan = section("Director loan");
    expect(loan.getByText("Loan rules")).toBeInTheDocument();
    fireEvent.change(loan.getByLabelText(/Interest rate/), { target: { value: "4" } });
    fireEvent.click(loan.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0][1].json).toEqual({ loan_interest_rate: 4 });
  });
});
