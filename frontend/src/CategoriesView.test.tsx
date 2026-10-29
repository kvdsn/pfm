import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CategoriesView } from "./CategoriesView";
import type { Budget, ManagedCategory } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const categoriesPayload = (): ManagedCategory[] => [
  { id: 1, name: "Groceries", transaction_count: 5, rules: [] },
  { id: 2, name: "Uncategorized", transaction_count: 2, rules: [] },
  { id: 3, name: "Dining", transaction_count: 1, rules: [] },
];

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url.includes("category-management")) return categoriesPayload();
    if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
    if (url === "/api/categories") return { id: 9, name: "Pets" };
    if (url.startsWith("/api/categories")) return { status: "ok" };
    throw new Error(`Unexpected API call: ${url}`);
  });
});

afterEach(cleanup);

const renderView = () => {
  const props = {
    accountType: "personal" as const,
    onNotice: vi.fn(),
    onError: vi.fn(),
    onChanged: vi.fn(async () => {}),
    onEditRules: vi.fn(),
    onClose: vi.fn(),
  };
  render(<CategoriesView {...props} />);
  return props;
};

describe("CategoriesView", () => {
  it("lists Uncategorized first, protected, and renames other categories on Enter", async () => {
    const { onNotice } = renderView();
    const names = await screen.findAllByLabelText(/^Name of category/);
    expect(names.map((input) => (input as HTMLInputElement).value)).toEqual(["Dining", "Groceries"]);
    expect(screen.getByText("Uncategorized", { selector: ".protected-category-name" })).toBeInTheDocument();

    fireEvent.change(names[1], { target: { value: "Food" } });
    fireEvent.keyDown(names[1], { key: "Enter" });
    fireEvent.blur(names[1]);
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Renamed Groceries to Food."));
    expect(apiMock).toHaveBeenCalledWith("/api/categories/1", { method: "PATCH", json: { name: "Food" } });
  });

  it("selects Uncategorized again after another category was selected", async () => {
    renderView();
    expect(await screen.findByRole("heading", { name: "Uncategorized" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Name of category Dining"));
    expect(await screen.findByRole("heading", { name: "Dining" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Uncategorized", { selector: ".protected-category-name" }));
    expect(await screen.findByRole("heading", { name: "Uncategorized" })).toBeInTheDocument();
  });

  it("merges a category after confirmation and adds new ones", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onNotice } = renderView();
    fireEvent.change(await screen.findByLabelText("Merge Dining into"), { target: { value: "1" } });
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Merged Dining into Groceries."));
    expect(apiMock).toHaveBeenCalledWith("/api/categories/merge", {
      method: "POST",
      json: { account_type: "personal", source_id: 3, target_id: 1 },
    });

    const add = screen.getByLabelText("New category name");
    fireEvent.change(add, { target: { value: "Pets" } });
    fireEvent.keyDown(add, { key: "Enter" });
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Added Pets."));
    confirm.mockRestore();
  });

  it("moves the selected category with the arrow keys, but not while typing in a field", async () => {
    renderView();
    expect(await screen.findByRole("heading", { name: "Uncategorized" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByRole("heading", { name: "Dining" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByRole("heading", { name: "Groceries" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByRole("heading", { name: "Dining" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("New category name"), { key: "ArrowDown" });
    expect(screen.getByRole("heading", { name: "Dining" })).toBeInTheDocument();
  });

  it("clears the filter and leaves the field on Escape without closing the view", async () => {
    const { onClose } = renderView();
    const filter = await screen.findByLabelText("Filter categories");
    filter.focus();
    fireEvent.change(filter, { target: { value: "gro" } });
    expect(screen.queryByLabelText("Name of category Dining")).not.toBeInTheDocument();
    fireEvent.keyDown(filter, { key: "Escape" });
    expect(filter).toHaveValue("");
    expect(filter).not.toHaveFocus();
    expect(screen.getByLabelText("Name of category Dining")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("focuses the new category field when the new item signal changes", async () => {
    const props = { accountType: "personal" as const, onNotice: vi.fn(), onError: vi.fn(), onChanged: vi.fn(async () => {}), onEditRules: vi.fn(), onClose: vi.fn() };
    const { rerender } = render(<CategoriesView {...props} newItemSignal={0} />);
    await screen.findByLabelText("New category name");
    expect(screen.getByLabelText("New category name")).not.toHaveFocus();
    rerender(<CategoriesView {...props} newItemSignal={1} />);
    expect(screen.getByLabelText("New category name")).toHaveFocus();
  });

  it("toggles a category as income, disabled for Uncategorized, and tolerates a missing flag", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("category-management")) {
        return [
          { id: 1, name: "Groceries", transaction_count: 5, rules: [] },
          { id: 2, name: "Uncategorized", transaction_count: 2, rules: [] },
          { id: 3, name: "Salary", is_income: true, transaction_count: 1, rules: [] },
        ];
      }
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      return { status: "updated" };
    });
    const { onNotice, onChanged } = renderView();
    const groceries = await screen.findByLabelText("Count Groceries as income");
    expect(groceries).not.toBeChecked();
    expect(screen.getByLabelText("Count Salary as income")).toBeChecked();
    expect(screen.getByLabelText("Count Uncategorized as income")).toBeDisabled();

    fireEvent.click(groceries);
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Groceries now counts as income."));
    expect(apiMock).toHaveBeenCalledWith("/api/categories/1", { method: "PATCH", json: { is_income: true } });
    expect(onChanged).toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("Count Salary as income"));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Salary now counts as expense."));
    expect(apiMock).toHaveBeenCalledWith("/api/categories/3", { method: "PATCH", json: { is_income: false } });
  });
});

describe("CategoriesView budgets", () => {
  const renderBudgets = (budgets: Budget[] = [{ category_id: 1, year: "2026", amount: "2000.00" }]) => {
    apiMock.mockImplementation(async (url: string, init?: { json?: { amount: string | null } }) => {
      if (url.includes("category-management")) return categoriesPayload();
      if (url.startsWith("/api/transactions")) return { total: 0, items: [] };
      if (url === "/api/budgets") {
        const text = (init?.json?.amount ?? "").replace(/\./g, "").replace(",", ".");
        return { category_id: 1, year: "2026", amount: Number(text) ? Number(text).toFixed(2) : null };
      }
      throw new Error(`Unexpected API call: ${url}`);
    });
    const props = {
      accountType: "personal" as const,
      year: "2026",
      budgets,
      yearTotals: new Map([[1, 1234]]),
      onNotice: vi.fn(),
      onError: vi.fn(),
      onChanged: vi.fn(async () => {}),
      onEditRules: vi.fn(),
      onClose: vi.fn(),
    };
    render(<CategoriesView {...props} />);
    return props;
  };
  const budgetPut = (amount: string) => ({
    method: "PUT",
    json: { account_type: "personal", category_id: 1, year: "2026", amount },
  });

  it("shows each budget in a column and disables Uncategorized", async () => {
    renderBudgets();
    expect(await screen.findByRole("columnheader", { name: "Budget 2026" })).toBeInTheDocument();
    expect(screen.getByLabelText("Budget 2026 for Groceries")).toHaveValue("2.000");
    expect(screen.getByLabelText("Budget 2026 for Dining")).toHaveValue("");
    expect(screen.getByLabelText("Budget 2026 for Dining")).toHaveAttribute("placeholder", "—");
    expect(screen.getByLabelText("Budget 2026 for Uncategorized")).toBeDisabled();
  });

  it("has no budget column without a year", async () => {
    renderView();
    await screen.findAllByLabelText(/^Name of category/);
    expect(screen.queryByRole("columnheader", { name: /^Budget/ })).not.toBeInTheDocument();
  });

  it("saves the typed text on Enter and says so", async () => {
    const { onNotice, onChanged } = renderBudgets();
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.change(input, { target: { value: "1.500" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Budget for Groceries in 2026 set to € 1.500."));
    expect(apiMock).toHaveBeenCalledWith("/api/budgets", budgetPut("1.500"));
    expect(apiMock.mock.calls.filter(([url]) => url === "/api/budgets")).toHaveLength(1);
    expect(onChanged).toHaveBeenCalled();
  });

  it("sends decimal text as typed and shows cents in the notice", async () => {
    const { onNotice } = renderBudgets();
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.change(input, { target: { value: "1500,50" } });
    fireEvent.blur(input);
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Budget for Groceries in 2026 set to € 1.500,50."));
    expect(apiMock).toHaveBeenCalledWith("/api/budgets", budgetPut("1500,50"));
  });

  it("saves on blur", async () => {
    renderBudgets([]);
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.change(input, { target: { value: "300" } });
    fireEvent.blur(input);
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/budgets", budgetPut("300")));
  });

  it("reverts on Escape without saving", async () => {
    renderBudgets();
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveValue("2.000");
    fireEvent.blur(input);
    expect(apiMock.mock.calls.some(([url]) => url === "/api/budgets")).toBe(false);
  });

  it("removes the budget when the field is cleared", async () => {
    const { onNotice } = renderBudgets();
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Budget for Groceries in 2026 removed."));
    expect(apiMock).toHaveBeenCalledWith("/api/budgets", budgetPut(""));
  });

  it("does nothing when the text is unchanged", async () => {
    renderBudgets();
    const input = await screen.findByLabelText("Budget 2026 for Groceries");
    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(apiMock.mock.calls.some(([url]) => url === "/api/budgets")).toBe(false);
  });

  it("shows the budget, what is used and what is left in the details", async () => {
    renderBudgets();
    fireEvent.click(await screen.findByLabelText("Name of category Groceries"));
    expect(await screen.findByText("Budget 2026: € 2.000 · used € 1.234 (62%) · € 766 left")).toBeInTheDocument();
  });

  it("says how far a budget is exceeded", async () => {
    renderBudgets([{ category_id: 1, year: "2026", amount: "1000.00" }]);
    fireEvent.click(await screen.findByLabelText("Name of category Groceries"));
    expect(await screen.findByText("Budget 2026: € 1.000 · used € 1.234 (123%) · € 234 over")).toBeInTheDocument();
  });
});
