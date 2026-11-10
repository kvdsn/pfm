import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));
import { TransactionsPanel } from "./TransactionsPanel";
import type { FollowUpFilter, Transaction } from "./types";
import type { TransactionSort } from "./transactionSort";

function transaction(id: number, overrides: Partial<Transaction>): Transaction {
  return {
    id,
    booking_date: "2025-03-01",
    value_date: "2025-03-01",
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
  };
}

function renderPanel(
  transactions: Transaction[],
  activeTransactionId: number | null = null,
  {
    onOpenRule = vi.fn(),
    onActivate = vi.fn(),
    search = "",
    selectedIds = new Set<number>(),
    onToggleSelected = vi.fn(),
    onSelectAll = vi.fn(),
    onAnnotate = vi.fn(async () => true),
    followUpFilter = "all" as FollowUpFilter,
    openFollowUps = null as number | null,
    onFollowUpFilter = vi.fn(),
    noteOpenId = null as number | null,
    onOpenNote = vi.fn(),
    onCloseNote = vi.fn(),
    sort = { key: "date", direction: "desc" } as TransactionSort | null,
    onSort = vi.fn(),
  } = {},
) {
  return render(
    <TransactionsPanel
      year="2026"
      transactions={transactions}
      total={transactions.length}
      sort={sort}
      onSort={onSort}
      search={search}
      onSearch={vi.fn()}
      searchInput={{ current: null }}
      selectedCategory={null}
      onClearCategory={vi.fn()}
      activeTransactionId={activeTransactionId}
      onActivate={onActivate}
      onRowClick={vi.fn()}
      rangeIds={new Set()}
      onEditCategory={vi.fn()}
      isUncategorized={() => false}
      rowRefs={{ current: new Map() }}
      onRuleFromSearch={vi.fn()}
      onOpenRule={onOpenRule}
      selectedIds={selectedIds}
      onToggleSelected={onToggleSelected}
      onSelectAll={onSelectAll}
      selectionBar={null}
      categoryEditor={null}
      ruleOffer={null}
      followUpFilter={followUpFilter}
      openFollowUps={openFollowUps}
      onFollowUpFilter={onFollowUpFilter}
      onAnnotate={onAnnotate}
      noteInput={{ current: null }}
      noteOpenId={noteOpenId}
      onOpenNote={onOpenNote}
      onCloseNote={onCloseNote}
    />,
  );
}

afterEach(cleanup);

describe("TransactionsPanel Set by column", () => {
  it("shows the rule text, Hand, or a dash with explanatory titles", () => {
    renderPanel([
      transaction(1, { category_source: "rule", category_rule: "Delhaize", category_rule_id: 7, category_rule_position: 3 }),
      transaction(2, { category_source: "manual" }),
      transaction(3, { category_source: "default" }),
    ]);
    expect(screen.getByRole("columnheader", { name: "Set by" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open rule #3, Delhaize" }).closest("td"))
      .toHaveAttribute("title", "Categorized by rule “Delhaize”");
    expect(screen.getByText("Rule #3")).toBeInTheDocument();
    expect(screen.getByText("Hand")).toHaveAttribute("title", "Set by hand");
    expect(screen.getByText("—")).toHaveAttribute("title", "No rule matched");
  });

  it("opens the rule from its link without activating the row", () => {
    const onOpenRule = vi.fn();
    const onActivate = vi.fn();
    renderPanel(
      [transaction(1, { category_source: "rule", category_rule: "Delhaize", category_rule_id: 7, category_rule_position: 3 })],
      null,
      { onOpenRule, onActivate },
    );
    fireEvent.click(screen.getByRole("button", { name: "Open rule #3, Delhaize" }));
    expect(onOpenRule).toHaveBeenCalledWith(7);
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("shows plain Rule when the rule no longer exists", () => {
    renderPanel([transaction(1, { category_source: "rule", category_rule: "Delhaize" })]);
    expect(screen.queryByRole("button", { name: /Open rule/ })).not.toBeInTheDocument();
    expect(screen.getByText("Rule")).toHaveAttribute("title", "Categorized by rule “Delhaize”");
  });

  it("spans the details dock over every column without a category line", () => {
    const { container } = renderPanel([transaction(1, { category_source: "manual", description: "Card payment" })], 1);
    expect(container.querySelector("tr.transaction-dock-row td")).toHaveAttribute("colspan", "6");
    expect(screen.queryByText("Category", { selector: "dt" })).not.toBeInTheDocument();
    // Empty fields still show, so the dock keeps its height between transactions.
    expect(screen.getByText("Memo", { selector: "dt" }).nextElementSibling).toHaveTextContent("—");
  });

  it("shows no details dock while nothing is selected", () => {
    const { container } = renderPanel([transaction(1, {})]);
    expect(container.querySelector("tfoot")).not.toBeInTheDocument();
  });
});

describe("TransactionsPanel sorting", () => {
  it("marks the sorted column and sorts from the Date and Amount headers", () => {
    const onSort = vi.fn();
    renderPanel([transaction(1, {})], null, { sort: { key: "amount", direction: "asc" }, onSort });
    const date = screen.getByRole("columnheader", { name: /Date/ });
    const amount = screen.getByRole("columnheader", { name: /Amount/ });
    expect(date).toHaveAttribute("aria-sort", "none");
    expect(amount).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(within(date).getByRole("button"));
    fireEvent.click(within(amount).getByRole("button"));
    expect(onSort.mock.calls).toEqual([["date"], ["amount"]]);
  });
});

describe("TransactionsPanel empty state", () => {
  it("names the search and category and offers to clear each", () => {
    const onSearch = vi.fn();
    const onClearCategory = vi.fn();
    render(
      <TransactionsPanel
        year="2026"
        transactions={[]}
        total={0}
        sort={null}
        onSort={vi.fn()}
        search=" zara "
        onSearch={onSearch}
        searchInput={{ current: null }}
        selectedCategory={{ category_id: 3, flow_type: "expense", name: "Shopping" }}
        onClearCategory={onClearCategory}
        activeTransactionId={null}
        onActivate={vi.fn()}
        onRowClick={vi.fn()}
        rangeIds={new Set()}
        onEditCategory={vi.fn()}
        isUncategorized={() => false}
        rowRefs={{ current: new Map() }}
        onRuleFromSearch={vi.fn()}
        onOpenRule={vi.fn()}
        selectedIds={new Set()}
        onToggleSelected={vi.fn()}
        onSelectAll={vi.fn()}
        selectionBar={null}
        categoryEditor={null}
        ruleOffer={null}
        followUpFilter="all"
        openFollowUps={null}
        onFollowUpFilter={vi.fn()}
        onAnnotate={vi.fn(async () => true)}
        noteInput={{ current: null }}
        noteOpenId={null}
        onOpenNote={vi.fn()}
        onCloseNote={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No transactions match “zara” in Shopping in 2026");
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(onSearch).toHaveBeenCalledWith("");
    fireEvent.click(screen.getByRole("button", { name: "Show all categories" }));
    expect(onClearCategory).toHaveBeenCalled();
  });
});

describe("TransactionsPanel Make rule", () => {
  it("is offered for text searches and hidden for amount and range searches", () => {
    for (const search of ["zara", "bol 25", "12.05.2026", "4411"]) {
      const { unmount } = renderPanel([transaction(1, {})], null, { search });
      expect(screen.getByRole("button", { name: "Make rule" })).toBeInTheDocument();
      unmount();
    }
    for (const search of ["41,50", "-41,00", "+41", "€41", "41 €", "1.250,00", "40..50", "40-50", "40 - 50", "-50..-40"]) {
      const { unmount } = renderPanel([transaction(1, {})], null, { search });
      expect(screen.queryByRole("button", { name: "Make rule" }), search).not.toBeInTheDocument();
      unmount();
    }
  });
});

describe("TransactionsPanel selection", () => {
  const rows = () => [
    transaction(1, {}),
    transaction(2, { own_transfer: true }),
    transaction(3, { itemized: true }),
    transaction(4, {}),
  ];

  it("shows the checkbox column while searching, or once something is selected", () => {
    const first = renderPanel(rows());
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    first.unmount();
    const second = renderPanel(rows(), null, { search: "mer" });
    expect(screen.getByRole("checkbox", { name: "Select all listed transactions" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Merchant 1 2025-03-01" })).toBeInTheDocument();
    second.unmount();
    const { container } = renderPanel(rows(), 1, { selectedIds: new Set([1]) });
    expect(screen.getByRole("checkbox", { name: "Select Merchant 1 2025-03-01" })).toBeChecked();
    expect(container.querySelector("tr.transaction-dock-row td")).toHaveAttribute("colspan", "7");
  });

  it("selects all listed selectable rows without a search", () => {
    const onSelectAll = vi.fn();
    renderPanel(rows(), null, { selectedIds: new Set([1]), onSelectAll });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all listed transactions" }));
    expect(onSelectAll).toHaveBeenCalledWith(true);
  });

  it("keeps six columns without a search or selection", () => {
    const { container } = renderPanel(rows(), 1);
    expect(container.querySelector("tr.transaction-dock-row td")).toHaveAttribute("colspan", "6");
  });

  it("gives own-account transfers and itemized card payments no checkbox, and widens the details dock", () => {
    const { container } = renderPanel(rows(), 4, { search: "mer" });
    expect(screen.getAllByRole("checkbox").filter((box) => box.getAttribute("aria-label")?.startsWith("Select "))).toHaveLength(3);
    expect(screen.queryByRole("checkbox", { name: /Merchant 2/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Merchant 3/ })).not.toBeInTheDocument();
    expect(container.querySelector("tr.transaction-dock-row td")).toHaveAttribute("colspan", "7");
  });

  it("selects only the selectable rows with select-all, and is indeterminate when some are selected", () => {
    const onSelectAll = vi.fn();
    const { unmount } = renderPanel(rows(), null, { search: "mer", onSelectAll });
    const all = screen.getByRole("checkbox", { name: "Select all listed transactions" }) as HTMLInputElement;
    expect(all.checked).toBe(false);
    expect(all.indeterminate).toBe(false);
    fireEvent.click(all);
    expect(onSelectAll).toHaveBeenCalledWith(true);
    unmount();

    const partial = renderPanel(rows(), null, { search: "mer", selectedIds: new Set([1]), onSelectAll });
    const some = screen.getByRole("checkbox", { name: "Select all listed transactions" }) as HTMLInputElement;
    expect(some.checked).toBe(false);
    expect(some.indeterminate).toBe(true);
    partial.unmount();

    renderPanel(rows(), null, { search: "mer", selectedIds: new Set([1, 4]), onSelectAll });
    const full = screen.getByRole("checkbox", { name: "Select all listed transactions" }) as HTMLInputElement;
    expect(full.checked).toBe(true);
    expect(full.indeterminate).toBe(false);
    fireEvent.click(full);
    expect(onSelectAll).toHaveBeenLastCalledWith(false);
  });

  it("toggles from a checkbox without activating the row", () => {
    const onActivate = vi.fn();
    const onToggleSelected = vi.fn();
    renderPanel(rows(), null, { search: "mer", onActivate, onToggleSelected });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Merchant 4 2025-03-01" }));
    expect(onToggleSelected).toHaveBeenCalledWith(4);
    expect(onActivate).not.toHaveBeenCalled();
  });
});

describe("TransactionsPanel dates and one-line rows", () => {
  it("shows dd/mm with the ISO date in the tooltip, and the year when listing all years", () => {
    const { container } = renderPanel([transaction(1, { booking_date: "2025-03-01" })]);
    const cell = container.querySelector(".transaction-date")!;
    expect(cell).toHaveTextContent("01/03");
    expect(cell).toHaveAttribute("title", "2025-03-01");
    cleanup();
    const all = renderPanel([transaction(1, { booking_date: "2025-03-01" })], null, { followUpFilter: "open" });
    expect(all.container.querySelector(".transaction-date")).toHaveTextContent("01/03/25");
  });

  it("puts merchant and details on one line, with the full text in the tooltip", () => {
    const { container } = renderPanel([transaction(1, { details: "Order 42" })]);
    const cell = container.querySelector(".transaction-summary")!;
    expect(cell.querySelectorAll(".transaction-line")).toHaveLength(1);
    expect(cell).toHaveTextContent("Merchant 1 · Order 42");
    expect(cell).toHaveAttribute("title", "Merchant 1 · Order 42");
  });

  it("has no separate actions line in the table head, and puts the controls in the dock", () => {
    const { container } = renderPanel([transaction(1, {})], 1);
    expect(container.querySelector("thead .transaction-actions")).toBeNull();
    expect(container.textContent).not.toContain("Select a transaction to add a note");
    const dock = container.querySelector(".transaction-dock") as HTMLElement;
    expect(within(dock).getByRole("group", { name: "Selected transaction" })).toBeInTheDocument();
    expect(within(dock).getByRole("button", { name: "Add note" })).toBeInTheDocument();
    expect(within(dock).getByRole("checkbox", { name: "Follow up" })).toBeInTheDocument();
  });

  it("shows no hint and no controls while nothing is selected", () => {
    const { container } = renderPanel([transaction(1, {})], null);
    expect(container.querySelector(".transaction-actions")).toBeNull();
  });
});

describe("TransactionsPanel notes and follow-ups", () => {
  const noteField = () => screen.getByRole("textbox", { name: "Note" });

  it("saves the note on blur and on Enter, trimmed, and not twice", () => {
    const onAnnotate = vi.fn(async () => true);
    renderPanel([transaction(1, { note: "old" })], 1, { onAnnotate });
    expect(noteField()).toHaveValue("old");
    expect(noteField()).toHaveAttribute("maxLength", "500");
    fireEvent.change(noteField(), { target: { value: " ask employer " } });
    fireEvent.keyDown(noteField(), { key: "Enter" });
    fireEvent.blur(noteField());
    expect(onAnnotate).toHaveBeenCalledTimes(1);
    expect(onAnnotate).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), { note: "ask employer" });
    fireEvent.change(noteField(), { target: { value: "" } });
    fireEvent.blur(noteField());
    expect(onAnnotate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }), { note: "" });
  });

  it("shows the note field only with a note, or once Add note opens it", () => {
    const onOpenNote = vi.fn();
    const first = renderPanel([transaction(1, {})], 1, { onOpenNote });
    expect(screen.queryByRole("textbox", { name: "Note" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(onOpenNote).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
    first.unmount();
    const onCloseNote = vi.fn();
    renderPanel([transaction(1, {})], 1, { noteOpenId: 1, onCloseNote });
    fireEvent.blur(noteField());
    expect(onCloseNote).toHaveBeenCalled();
  });

  it("offers the actions only once a transaction is selected", () => {
    renderPanel([transaction(1, { note: "x" })], null);
    expect(screen.queryByRole("checkbox", { name: "Follow up" })).not.toBeInTheDocument();
    cleanup();
    renderPanel([transaction(1, { note: "x" })], 1);
    expect(screen.getByRole("button", { name: "Edit note" })).toBeInTheDocument();
  });

  it("does not save an unchanged note", () => {
    const onAnnotate = vi.fn(async () => true);
    renderPanel([transaction(1, { note: "same" })], 1, { onAnnotate });
    fireEvent.blur(noteField());
    expect(onAnnotate).not.toHaveBeenCalled();
  });

  it("reverts on Escape without saving or letting Escape travel further", () => {
    const onAnnotate = vi.fn(async () => true);
    const outer = vi.fn();
    window.addEventListener("keydown", outer);
    renderPanel([transaction(1, { note: "kept" })], 1, { onAnnotate });
    fireEvent.change(noteField(), { target: { value: "typed" } });
    fireEvent.keyDown(noteField(), { key: "Escape" });
    window.removeEventListener("keydown", outer);
    expect(noteField()).toHaveValue("kept");
    expect(onAnnotate).not.toHaveBeenCalled();
    expect(outer).not.toHaveBeenCalled();
  });

  it("ticks and unticks Follow up, and offers Done on an open one", () => {
    const onAnnotate = vi.fn(async () => true);
    const first = renderPanel([transaction(1, {})], 1, { onAnnotate });
    fireEvent.click(screen.getByRole("checkbox", { name: "Follow up" }));
    expect(onAnnotate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }), { follow_up: "open" });
    expect(within(document.querySelector(".transaction-actions") as HTMLElement).queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
    first.unmount();
    renderPanel([transaction(1, { follow_up: "open", follow_up_at: "2026-03-05T10:00:00" })], 1, { onAnnotate });
    expect(screen.getByRole("checkbox", { name: "Follow up" })).toBeChecked();
    fireEvent.click(within(document.querySelector(".transaction-actions") as HTMLElement).getByRole("button", { name: "Done" }));
    expect(onAnnotate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }), { follow_up: "done" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Follow up" }));
    expect(onAnnotate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }), { follow_up: null });
  });

  it("treats a row without annotation fields (an older server) as not flagged", () => {
    const row = transaction(1, {}) as Partial<Transaction>;
    delete row.follow_up;
    delete row.follow_up_at;
    renderPanel([row as Transaction], 1);
    expect(screen.getByRole("checkbox", { name: "Follow up" })).not.toBeChecked();
    expect(document.querySelector(".annotation-marker")).toBeNull();
  });

  it("shows when a follow-up was done and reopens it", () => {
    const onAnnotate = vi.fn(async () => true);
    renderPanel([transaction(1, { follow_up: "done", follow_up_at: "2026-03-05T10:00:00" })], 1, { onAnnotate });
    expect(screen.getByText("Done on 2026-03-05")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    expect(onAnnotate).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), { follow_up: "open" });
  });

  it("marks rows with a note or follow-up and shows the note as tooltip", () => {
    renderPanel([
      transaction(1, { follow_up: "open", note: "ask employer" }),
      transaction(2, { follow_up: "done" }),
      transaction(3, { note: "just a note" }),
      transaction(4, {}),
    ]);
    const markers = screen.getAllByRole("img");
    expect(markers).toHaveLength(3);
    expect(markers[0]).toHaveAttribute("title", "ask employer");
    expect(markers[0]).toHaveClass("open");
    expect(markers[1]).toHaveClass("done");
    expect(markers[2]).toHaveClass("note");
    expect(markers[2]).toHaveAttribute("title", "just a note");
  });
});

describe("TransactionsPanel follow-up filter", () => {
  it("shows the open count and reports the chosen filter", () => {
    const onFollowUpFilter = vi.fn();
    renderPanel([transaction(1, {})], null, { openFollowUps: 3, onFollowUpFilter });
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Follow-ups 3" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Follow-ups 3" }));
    expect(onFollowUpFilter).toHaveBeenCalledWith("open");
    fireEvent.click(within(screen.getByRole("group", { name: "Show transactions" })).getByRole("button", { name: "Done" }));
    expect(onFollowUpFilter).toHaveBeenCalledWith("done");
  });

  it("leaves the number out when there are none or it is unknown, and says all years", () => {
    const { unmount } = renderPanel([transaction(1, {})], null, { openFollowUps: 0 });
    expect(screen.getByRole("button", { name: "Follow-ups" })).toBeInTheDocument();
    unmount();
    renderPanel([transaction(1, {})], null, { followUpFilter: "open" });
    expect(screen.getByRole("button", { name: "Follow-ups" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: /all years/ })).toBeInTheDocument();
  });
});

describe("TransactionsPanel settlement detail", () => {
  const statement = (count: number) => ({
    id: 1, period: "2026-01-07", debit_date: "2026-01-16", import_id: 3, purchase_count: count, removed_count: 1,
    total: "-100.00", carried_credit: "68.25", due: "31.75", status: "paid", settlement: null,
    card: "Belfius card •••• 5000",
    purchases: Array.from({ length: count }, (_, index) => transaction(100 + index, {
      booking_date: "2026-01-03", account: "Belfius card", merchant: `Shop ${index + 1}`, amount: "-1.00",
      category: index === 0 ? "Uncategorized" : "Groceries",
    })),
  });

  it("lists the statement's purchases as table rows under the selected itemized settlement", async () => {
    apiMock.mockReset();
    apiMock.mockResolvedValue(statement(12));
    renderPanel([transaction(5, { itemized: true, merchant: "Belfius card payment" })], 5);
    expect(screen.getByText("Itemized card payment")).toBeInTheDocument();
    // Selecting the settlement only offers the purchases, so moving through the rows never shifts them.
    expect(apiMock).not.toHaveBeenCalled();
    const toggle = screen.getByRole("button", { name: "Show the statement's purchases" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide the statement's purchases" })).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("Itemized on Belfius card •••• 5000 · statement debited 2026-01-16 · 12 purchases")).toBeInTheDocument();
    expect(apiMock).toHaveBeenCalledWith("/api/transactions/5/statement");
    const purchaseRows = () => document.querySelectorAll("tr.statement-purchase-row");
    expect(purchaseRows()).toHaveLength(10);
    // The purchases use the table's own columns: date, account, transaction, amount, category, set by.
    expect([...purchaseRows()[0].querySelectorAll("td")].map((cell) => cell.className)).toEqual([
      "transaction-date", "transaction-account", "transaction-summary", "transaction-amount negative", "transaction-category", "transaction-source",
    ]);
    expect(screen.getAllByText("Uncategorized")[0]).toHaveClass("statement-category", "uncategorized-label");
    const sums = [...document.querySelectorAll("tr.statement-sum-row")].map((row) => row.textContent);
    expect(sums).toEqual([
      "Total, including 1 removed purchase-€ 100,00", "Credit from the previous statement€ 68,25", "Paid-€ 31,75",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Show all 12 purchases" }));
    expect(purchaseRows()).toHaveLength(12);
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("stays quiet when the statement cannot be loaded and skips normal transactions", async () => {
    apiMock.mockReset();
    apiMock.mockRejectedValue(new Error("nope"));
    renderPanel([transaction(5, { itemized: true })], 5);
    fireEvent.click(screen.getByRole("button", { name: "Show the statement's purchases" }));
    await waitFor(() => expect(screen.getByText("The statement could not be loaded.")).toBeInTheDocument());
    cleanup();
    apiMock.mockClear();
    renderPanel([transaction(6, {})], 6);
    expect(screen.queryByRole("button", { name: /statement's purchases/ })).not.toBeInTheDocument();
    expect(apiMock).not.toHaveBeenCalled();
  });
});
