import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TriageView } from "./TriageView";
import type { Category, UncategorizedGroup } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const categories: Category[] = [
  { id: 1, name: "Uncategorized", rule_flow_type: null },
  { id: 2, name: "Groceries", rule_flow_type: "expense" },
  { id: 3, name: "Gifts", rule_flow_type: null },
  { id: 4, name: "Refunds", rule_flow_type: "income" },
];

const group = (overrides: Partial<UncategorizedGroup> = {}): UncategorizedGroup => ({
  merchant: "Delhaize",
  kind: "Card",
  count: 2,
  total: "-45.50",
  first_date: "2026-03-01",
  last_date: "2026-03-09",
  transaction_ids: [11, 12],
  transactions: [
    { id: 12, date: "2026-03-09", amount: "-20.00", details: "Card · Brussels" },
    { id: 11, date: "2026-03-01", amount: "-25.50", details: "Card · Ghent" },
  ],
  sample_details: ["Card · Brussels", "Card · Ghent"],
  suggestion: { category_id: 2, name: "Groceries", count: 5 },
  merchant_rule_matches: 2,
  ...overrides,
});

let groups: UncategorizedGroup[] = [];

beforeEach(() => {
  apiMock.mockReset();
  groups = [
    group(),
    group({
      merchant: "", kind: "Transfer", count: 1, total: "10.00", first_date: "2026-02-01", last_date: "2026-02-01",
      transaction_ids: [21], transactions: [{ id: 21, date: "2026-02-01", amount: "10.00", details: "Transfer" }],
      sample_details: ["Transfer"], suggestion: null, merchant_rule_matches: 0,
    }),
  ];
  apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
    if (url.startsWith("/api/uncategorized-groups")) return groups;
    if (url === "/api/transactions/bulk-category") return { updated: 2 };
    if (url === "/api/category-rules" && init?.method === "POST") return { id: 77 };
    if (url === "/api/category-rules/77/run") return { updated: 2, released: 0 };
    throw new Error(`Unexpected API call: ${url}`);
  });
});

afterEach(cleanup);

const renderView = () => {
  const props = { onNotice: vi.fn(), onError: vi.fn(), onChanged: vi.fn().mockResolvedValue(undefined), onClose: vi.fn() };
  render(<TriageView accountType="personal" categories={categories} {...props} />);
  return props;
};

const rows = () => [...screen.getByRole("table").querySelectorAll("tbody tr[data-group-key]")] as HTMLElement[];
const callsTo = (url: string) => apiMock.mock.calls.filter(([called]) => called === url);

describe("Triage view", () => {
  it("lists the groups with the transaction count and a preselected, marked suggestion", async () => {
    renderView();
    await screen.findByText("Delhaize");
    expect(screen.getByRole("heading", { name: /Uncategorized/ })).toHaveTextContent("3");
    expect(screen.getByText(/Grouped by merchant and kind, largest first/)).toBeInTheDocument();
    const [first, second] = rows();
    expect(first).toHaveTextContent("Delhaize");
    expect(first).toHaveTextContent("Card");
    expect(first).toHaveTextContent("-€ 45,50");
    expect(first).toHaveTextContent("2026-03-012026-03-09");
    expect(first).toHaveTextContent("Card · Brussels");
    expect(within(first).getByRole("button", { name: /Category for Delhaize/ })).toHaveTextContent("Groceriessuggested");
    expect(second).toHaveTextContent("Unknown merchant");
    expect(within(second).getByRole("button", { name: /Category for Unknown merchant/ })).toHaveTextContent("Choose category…");
    expect(within(second).getByRole("button", { name: "Set category" })).toBeDisabled();
  });

  it("expands a group to show its transactions", async () => {
    renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(rows()[0]);
    expect(screen.getByText("Card · Ghent", { selector: ".triage-transaction span" })).toBeInTheDocument();
    expect(screen.getByText("-€ 25,50")).toBeInTheDocument();
    fireEvent.click(rows()[0]);
    expect(screen.queryByText("-€ 25,50")).not.toBeInTheDocument();
  });

  it("Set category posts the group's transaction ids, then reloads and refreshes the app", async () => {
    const props = renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(within(rows()[0]).getByRole("button", { name: "Set category" }));
    await waitFor(() => expect(props.onNotice).toHaveBeenCalledWith("Categorized 2 transactions as Groceries."));
    expect(JSON.parse(callsTo("/api/transactions/bulk-category")[0][1].body ?? "null") ?? callsTo("/api/transactions/bulk-category")[0][1].json).toEqual({
      account_type: "personal", transaction_ids: [11, 12], category_id: 2,
    });
    expect(callsTo("/api/uncategorized-groups?account_type=personal").length).toBeGreaterThanOrEqual(2);
    expect(props.onChanged).toHaveBeenCalled();
  });

  it("choosing another category in the picker changes what is applied", async () => {
    renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(within(rows()[0]).getByRole("button", { name: /Category for Delhaize/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Category" }), { target: { value: "gift" } });
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Category" }), { key: "Enter" });
    expect(within(rows()[0]).getByRole("button", { name: /Category for Delhaize/ })).toHaveTextContent("Gifts");
    fireEvent.click(within(rows()[0]).getByRole("button", { name: "Set category" }));
    await waitFor(() => expect(callsTo("/api/transactions/bulk-category")).toHaveLength(1));
    expect(callsTo("/api/transactions/bulk-category")[0][1].json.category_id).toBe(3);
  });

  it("Make rule creates an exact merchant rule with the category's flow, then runs it", async () => {
    const props = renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(within(rows()[0]).getByRole("button", { name: "Make rule" }));
    await waitFor(() => expect(props.onNotice).toHaveBeenCalledWith("Added a rule for “Delhaize”; categorized 2 transactions as Groceries."));
    expect(callsTo("/api/category-rules")[0][1].json).toEqual({
      account_type: "personal", category_id: 2, match_text: "Delhaize", match_fields: ["merchant"],
      match_mode: "exact", flow_type: "expense",
    });
    expect(callsTo("/api/category-rules/77/run")[0][1]).toMatchObject({ method: "POST", json: { account_type: "personal" } });
    expect(props.onChanged).toHaveBeenCalled();
  });

  it("Make rule falls back to the sign of the amounts when the category has no flow type", async () => {
    groups = [group({ total: "30.00", suggestion: { category_id: 3, name: "Gifts", count: 1 } })];
    renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(screen.getByRole("button", { name: "Make rule" }));
    await waitFor(() => expect(callsTo("/api/category-rules/77/run")).toHaveLength(1));
    expect(callsTo("/api/category-rules")[0][1].json.flow_type).toBe("income");
  });

  it("disables Make rule for an unknown merchant", async () => {
    renderView();
    await screen.findByText("Delhaize");
    const unknown = rows()[1];
    fireEvent.click(within(unknown).getByRole("button", { name: /Category for Unknown merchant/ }));
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Category" }), { key: "Enter" });
    expect(within(unknown).getByRole("button", { name: "Set category" })).toBeEnabled();
    expect(within(unknown).getByRole("button", { name: "Make rule" })).toBeDisabled();
  });

  it("warns when the rule would reach beyond the group", async () => {
    groups = [group({ merchant_rule_matches: 5 }), group({ merchant: "Colruyt", merchant_rule_matches: 2 })];
    renderView();
    await screen.findByText("Delhaize");
    expect(rows()[0]).toHaveTextContent("rule also categorizes 3 others");
    expect(rows()[1]).not.toHaveTextContent("rule also categorizes");
  });

  it("moves the selection with the arrow keys and opens the picker with Enter", async () => {
    renderView();
    await screen.findByText("Delhaize");
    const expanded = (index: number) => rows()[index].querySelector(".triage-disclosure")?.getAttribute("aria-expanded");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(rows()[0]).toHaveClass("selected-rule");
    expect(expanded(0)).toBe("true");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(rows()[1]).toHaveClass("selected-rule");
    // Moving on collapses the group left behind and expands the new one.
    expect([expanded(0), expanded(1)]).toEqual(["false", "true"]);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(within(rows()[1]).getByRole("combobox", { name: "Category" })).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    groups = [];
    renderView();
    expect(await screen.findByText("Nothing to triage — every transaction has a category.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("closes with the × button", async () => {
    const props = renderView();
    await screen.findByText("Delhaize");
    fireEvent.click(screen.getByRole("button", { name: "Close triage" }));
    expect(props.onClose).toHaveBeenCalled();
  });
});
