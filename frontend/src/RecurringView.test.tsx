import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecurringView } from "./RecurringView";
import type { Category, LinkableRecurringItem, RecurringItem, RecurringList } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({
  api: apiMock,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

const categories: Category[] = [
  { id: 1, name: "Uncategorized", rule_flow_type: null },
  { id: 41, name: "Income insurance", rule_flow_type: "expense" },
  { id: 42, name: "Streaming", rule_flow_type: "expense" },
];

const history = (amount: string | null) =>
  Array.from({ length: 13 }, (_, index) => ({ period: `2025-${String(index + 1).padStart(2, "0")}`, amount: index % 2 ? amount : null }));

const item = (overrides: Partial<RecurringItem>): RecurringItem => ({
  id: null,
  status: "candidate",
  merchant: "Netflix",
  kind: "Card",
  cadence: "monthly",
  day_of_month: 12,
  category_id: null,
  category: null,
  amount: "-15.99",
  monthly_amount: "-15.99",
  amounts: [{ effective_date: "2025-06-12", amount: "-15.99" }],
  start_date: "2025-06-12",
  end_date: null,
  last_date: "2026-10-12",
  next_date: "2026-11-12",
  history: history("-15.99"),
  occurrences: 12,
  flags: [],
  linked: null,
  note: "",
  ...overrides,
});

const insurer = item({
  id: 12, status: "confirmed", merchant: "Verzekeraar", kind: "Direct debit", category_id: 41, category: "Income insurance",
  amount: "-150.00", monthly_amount: "-150.00", day_of_month: 3, start_date: "2025-01-03", next_date: "2026-11-03",
  amounts: [{ effective_date: "2025-01-03", amount: "-145.00" }, { effective_date: "2026-01-03", amount: "-150.00" }],
  flags: ["price_change"],
});
const netflix = item({ flags: ["new"] });
const spotify = item({ merchant: "Spotify", amount: "-10.99", monthly_amount: "-10.99" });
const old = item({ id: 20, status: "ignored", merchant: "Gym", kind: "Direct debit", amount: "-30.00", monthly_amount: "-30.00" });

let list: RecurringList;
const linkable: LinkableRecurringItem[] = [
  { id: 30, account_type: "kids", merchant: "Jan Peeters", kind: "Top-up", amount: "50.00", cadence: "monthly" },
];

beforeEach(() => {
  apiMock.mockReset();
  list = {
    monthly: { income: "350.00", expense: "-1240.50", net: "-890.50" },
    items: [insurer],
    candidates: [netflix, spotify],
    ignored: [old],
  };
  apiMock.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/recurring/linkable")) return linkable;
    if (url.startsWith("/api/recurring?")) return list;
    return {};
  });
});

afterEach(cleanup);

const renderView = () => {
  const props = { onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn() };
  render(<RecurringView accountType="personal" categories={categories} {...props} />);
  return props;
};
const calls = (method: string) => apiMock.mock.calls.filter(([, init]) => init?.method === method);

describe("RecurringView", () => {
  it("shows the monthly summary and the three sections", async () => {
    renderView();
    const summary = await screen.findByLabelText("Monthly total of confirmed payments");
    expect(summary).toHaveTextContent("€ 350,00");
    expect(summary).toHaveTextContent("-€ 1.240,50");
    expect(summary).toHaveTextContent("-€ 890,50");
    expect(within(screen.getByRole("table", { name: "Confirmed" })).getByText("Verzekeraar")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Confirmed" })).getByText("Price changed")).toBeInTheDocument();
    const suggested = screen.getByRole("table", { name: "Suggested" });
    expect(within(suggested).getByText("Netflix")).toBeInTheDocument();
    expect(within(suggested).getByText("New")).toBeInTheDocument();
    expect(within(suggested).getByText("Spotify")).toBeInTheDocument();
    expect(screen.queryByText("Gym")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 ignored" }));
    expect(within(screen.getByRole("table", { name: "Ignored" })).getByText("Gym")).toBeInTheDocument();
  });

  it("Confirm and Ignore post the candidate", async () => {
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Netflix" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0]).toEqual(["/api/recurring", { method: "POST", json: { account_type: "personal", merchant: "Netflix", kind: "Card", status: "confirmed" } }]);
    fireEvent.click(await screen.findByRole("button", { name: "Ignore Spotify" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(2));
    expect(calls("POST")[1][1].json).toEqual({ account_type: "personal", merchant: "Spotify", kind: "Card", status: "ignored" });
  });

  it("Restore deletes the decision", async () => {
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Show 1 ignored" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore Gym" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/api/recurring/20?account_type=personal");
  });

  it("saves an edited amount schedule", async () => {
    renderView();
    fireEvent.click(await screen.findByText("Verzekeraar"));
    fireEvent.change(screen.getByLabelText("Amount 2"), { target: { value: "-170,00" } });
    fireEvent.click(screen.getByRole("button", { name: "Add amount change" }));
    fireEvent.change(screen.getByLabelText("Effective date 3"), { target: { value: "2026-10-03" } });
    fireEvent.change(screen.getByLabelText("Amount 3"), { target: { value: "-175,50" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0][0]).toBe("/api/recurring/12");
    expect(calls("PATCH")[0][1].json).toEqual({
      account_type: "personal",
      cadence: "monthly",
      day_of_month: 3,
      category_id: 41,
      start_date: "2025-01-03",
      end_date: null,
      note: "",
      amounts: [
        { effective_date: "2025-01-03", amount: "-145.00" },
        { effective_date: "2026-01-03", amount: "-170,00" },
        { effective_date: "2026-10-03", amount: "-175,50" },
      ],
    });
  });

  it("shows the server's message when saving fails", async () => {
    renderView();
    fireEvent.click(await screen.findByText("Verzekeraar"));
    apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (init?.method === "PATCH") throw new Error("Amounts need unique dates");
      return url.startsWith("/api/recurring/linkable") ? linkable : list;
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Amounts need unique dates");
  });

  it("moves with the arrow keys and confirms a suggestion with Enter", async () => {
    renderView();
    await screen.findByText("Verzekeraar");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    const pane = screen.getByLabelText("Recurring payment details");
    expect(within(pane).getByRole("heading", { name: "Verzekeraar" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(within(screen.getByLabelText("Recurring payment details")).getByRole("heading", { name: "Netflix" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(within(screen.getByLabelText("Recurring payment details")).getByRole("heading", { name: "Verzekeraar" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(calls("POST")).toHaveLength(0);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][1].json.merchant).toBe("Netflix");
  });

  it("links an item to another entity's item", async () => {
    renderView();
    fireEvent.click(await screen.findByText("Verzekeraar"));
    fireEvent.change(screen.getByLabelText("Linked to"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT")).toHaveLength(1));
    expect(calls("PUT")[0]).toEqual(["/api/recurring/12/link", { method: "PUT", json: { account_type: "personal", linked_item_id: 30 } }]);
  });

  it("adds a payment by hand from the Add payment form", async () => {
    const created = item({ id: 40, status: "confirmed", merchant: "Kids top-up", kind: "", amount: "-400.00", monthly_amount: "-400.00" });
    apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (url.startsWith("/api/recurring/linkable")) return linkable;
      if (url === "/api/recurring/manual" && init?.method === "POST") {
        list = { ...list, items: [insurer, created] };
        return created;
      }
      if (url.startsWith("/api/recurring?")) return list;
      return {};
    });
    const { onNotice } = renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Add payment" }));
    const pane = screen.getByRole("complementary", { name: "Add a recurring payment" });
    expect(within(pane).getByLabelText(/Follows transactions of/)).toHaveFocus();
    fireEvent.change(within(pane).getByLabelText("Name"), { target: { value: "Kids top-up" } });
    fireEvent.change(within(pane).getByLabelText("Amount"), { target: { value: "-400" } });
    fireEvent.change(within(pane).getByLabelText("Start date"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(pane).getByLabelText("Category"), { target: { value: "42" } });
    fireEvent.click(within(pane).getByRole("button", { name: "Add payment" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Added Kids top-up."));
    expect(apiMock).toHaveBeenCalledWith("/api/recurring/manual", {
      method: "POST",
      json: {
        account_type: "personal", merchant: "Kids top-up", kind: "", cadence: "monthly", amount: "-400",
        start_date: "2026-11-01", end_date: null, day_of_month: null, category_id: 42, note: "",
      },
    });
    // The new payment is selected and editable.
    expect(await screen.findByRole("complementary", { name: "Recurring payment details" })).toHaveTextContent("Kids top-up");
  });

  it("opens the form on N and shows the server's message", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/recurring/linkable")) return linkable;
      if (url === "/api/recurring/manual") throw new Error("A recurring payment named Verzekeraar already exists");
      if (url.startsWith("/api/recurring?")) return list;
      return {};
    });
    const props = { onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn() };
    const { rerender } = render(<RecurringView accountType="personal" categories={categories} {...props} />);
    await screen.findByText("Verzekeraar");
    expect(screen.queryByRole("complementary", { name: "Add a recurring payment" })).not.toBeInTheDocument();
    rerender(<RecurringView accountType="personal" categories={categories} {...props} newItemSignal={1} />);
    const pane = await screen.findByRole("complementary", { name: "Add a recurring payment" });
    fireEvent.change(within(pane).getByLabelText("Name"), { target: { value: "Verzekeraar" } });
    fireEvent.change(within(pane).getByLabelText("Amount"), { target: { value: "-1" } });
    fireEvent.click(within(pane).getByRole("button", { name: "Add payment" }));
    expect(await within(pane).findByRole("alert")).toHaveTextContent("A recurring payment named Verzekeraar already exists");
    fireEvent.click(within(pane).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("complementary", { name: "Add a recurring payment" })).not.toBeInTheDocument();
  });

  it("adds a payment that follows a group of transactions, filled in from it", async () => {
    const lease = {
      merchant: "KBC", kind: "Lease payment", count: 4, first_date: "2026-08-04", last_date: "2026-10-07",
      last_amount: "-455.50", category_id: 42, category: "Streaming",
    };
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/recurring/linkable")) return linkable;
      if (url.startsWith("/api/recurring/groups")) return [lease];
      if (url === "/api/recurring/manual") return item({ id: 41, status: "confirmed", merchant: "KBC", kind: "Lease payment" });
      if (url.startsWith("/api/recurring?")) return list;
      return {};
    });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Add payment" }));
    const pane = screen.getByRole("complementary", { name: "Add a recurring payment" });
    const follows = within(pane).getByLabelText(/Follows transactions of/);
    await waitFor(() => expect(pane.querySelectorAll("datalist option")).toHaveLength(1));
    fireEvent.change(follows, { target: { value: "KBC · Lease payment (4 payments, last 2026-10-07, -€ 455,50)" } });
    expect(within(pane).getByLabelText("Name")).toHaveValue("KBC");
    expect(within(pane).getByLabelText("Name")).toBeDisabled();
    expect(within(pane).getByLabelText("Amount")).toHaveValue("-455.50");
    expect(within(pane).getByLabelText("Start date")).toHaveValue("2026-08-04");
    expect(within(pane).getByLabelText("Day of month")).toHaveValue(7);
    fireEvent.change(within(pane).getByLabelText("End date"), { target: { value: "2030-07-07" } });
    fireEvent.click(within(pane).getByRole("button", { name: "Add payment" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][1].json).toMatchObject({
      merchant: "KBC", kind: "Lease payment", amount: "-455.50", start_date: "2026-08-04", end_date: "2030-07-07",
      day_of_month: 7, category_id: 42,
    });
  });
});
