import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReimbursementsSection, shareCents } from "./Reimbursements";
import type { Category, ReimbursementClaim, Reimbursements } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({
  api: apiMock,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

const personal: Category[] = [
  { id: 1, name: "Uncategorized", rule_flow_type: null },
  { id: 10, name: "Energy", rule_flow_type: null },
  { id: 11, name: "Car", rule_flow_type: null },
];
const business: Category[] = [
  { id: 2, name: "Uncategorized", rule_flow_type: null },
  { id: 20, name: "Utilities", rule_flow_type: null },
];
const claim = (id: number, overrides: Partial<ReimbursementClaim> = {}): ReimbursementClaim => ({
  id, date: "2026-09-10", description: "Energy invoice", amount: "480.20", share: 60, source_transaction_id: 7, source: null,
  personal_category_id: 10, personal_category: "Energy", business_category_id: 20, business_category: "Utilities",
  expected_date: null, expected_on: "2026-10-10", paid_transaction_id: null, paid: null, note: "", ...overrides,
});
let data: Reimbursements;

beforeEach(() => {
  apiMock.mockReset();
  data = {
    claims: [
      claim(1),
      claim(2, { description: "EV charging", amount: "75.00", share: null, paid_transaction_id: 90, paid: { id: 90, date: "2026-10-05", merchant: "Own BV", amount: "75.00" } }),
    ],
    open_total: "480.20", paid_total_this_year: "75.00",
    candidates: [
      { id: 91, date: "2026-10-01", merchant: "Own BV", amount: "300.00" },
      { id: 92, date: "2026-10-02", merchant: "Own BV", amount: "480.20" },
    ],
  };
  apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
    if (url === "/api/reimbursements" && !init?.method) return data;
    if (url.startsWith("/api/categories?account_type=")) return url.endsWith("business") ? business : personal;
    if (url.startsWith("/api/transactions?")) {
      return { items: [
        { id: 7, booking_date: "2026-09-10", merchant: "Energy Co", amount: "-800.33", category_id: 10, category: "Energy" },
        { id: 8, booking_date: "2026-09-11", merchant: "Refund", amount: "5.00", category_id: 10, category: "Energy" },
      ] };
    }
    return {};
  });
});
afterEach(cleanup);

const renderSection = async () => {
  const props = { onChanged: vi.fn(), onNotice: vi.fn() };
  render(<ReimbursementsSection accountType="personal" categories={personal} {...props} />);
  await screen.findByRole("table", { name: "Claims" });
  return props;
};
const calls = (method: string) => apiMock.mock.calls.filter(([, init]) => init?.method === method);

describe("Reimbursements", () => {
  it("rounds a share of an expense half up", () => {
    expect(shareCents("-800.33", "60")).toBe(48020);
    expect(shareCents("-0.05", "50")).toBe(3);
    expect(shareCents("-10", "0")).toBeNull();
    expect(shareCents("-10", "abc")).toBeNull();
  });

  it("shows the totals and each claim's status", async () => {
    await renderSection();
    const totals = screen.getByLabelText("Reimbursement totals");
    expect(totals).toHaveTextContent("Business owes Personal € 480,20");
    expect(totals).toHaveTextContent("€ 75,00 repaid this year");
    const rows = within(screen.getByRole("table", { name: "Claims" })).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Open, expected 2026-10-10");
    expect(rows[2]).toHaveTextContent("Paid 2026-10-05");
  });

  it("adds a claim from a share of an expense, showing the amount live", async () => {
    const { onChanged } = await renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Add claim" }));
    fireEvent.change(screen.getByLabelText("Search Personal expenses"), { target: { value: "energy" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const list = await screen.findByRole("list", { name: "Expenses found" });
    expect(within(list).queryByText(/Refund/)).not.toBeInTheDocument();  // only expenses
    expect(apiMock.mock.calls.some(([url]) => String(url).includes("account_type=personal") && String(url).includes("q=energy"))).toBe(true);
    fireEvent.click(within(list).getByRole("button", { name: /Energy Co/ }));
    expect(screen.getByLabelText("Claim amount")).toHaveTextContent("€ 400,17");
    fireEvent.change(screen.getByLabelText("Share (%)"), { target: { value: "60" } });
    expect(screen.getByLabelText("Claim amount")).toHaveTextContent("€ 480,20");
    fireEvent.change(screen.getByLabelText("Business category"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("Expected repayment date"), { target: { value: "2026-11-25" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0]).toEqual(["/api/reimbursements", { method: "POST", json: {
      source_transaction_id: 7, share: 60, personal_category_id: 10, business_category_id: 20, expected_date: "2026-11-25",
    } }]);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("adds a claim by amount, which needs a Personal category", async () => {
    await renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Add claim" }));
    fireEvent.click(screen.getByLabelText("Amount"));
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-30" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "EV charging 2026 Q3" } });
    fireEvent.change(screen.getAllByLabelText("Amount").find((el) => el.tagName === "INPUT" && (el as HTMLInputElement).type === "text")!, { target: { value: "75,00" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose the Personal category.");
    expect(calls("POST")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Personal category"), { target: { value: "11" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][1].json).toEqual({
      date: "2026-09-30", description: "EV charging 2026 Q3", amount: "75,00", personal_category_id: 11,
    });
  });

  it("marks a claim paid from the candidates, matching amounts first, and reopens one", async () => {
    await renderSection();
    const select = screen.getByLabelText("Mark claim Energy invoice paid") as HTMLSelectElement;
    expect(Array.from(select.options).slice(1).map((option) => option.value)).toEqual(["92", "91"]);
    fireEvent.change(select, { target: { value: "92" } });
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0]).toEqual(["/api/reimbursements/1", { method: "PATCH", json: { paid_transaction_id: 92 } }]);
    fireEvent.click(screen.getByRole("button", { name: "Reopen claim EV charging" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(2));
    expect(calls("PATCH")[1]).toEqual(["/api/reimbursements/2", { method: "PATCH", json: { paid_transaction_id: null } }]);
  });

  it("deletes a claim", async () => {
    await renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Delete claim Energy invoice" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/api/reimbursements/1");
  });
});
