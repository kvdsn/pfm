import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountsView, daysSince } from "./AccountsView";
import type { ImportFlow } from "./Imports";
import type { AccountOverview, ImportPreview, ImportRecord } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const account = (id: number, overrides: Partial<AccountOverview> = {}): AccountOverview => ({
  id,
  account_type: "personal",
  bank: "KBC",
  kind: "bank",
  label: "KBC",
  masked: "BE00 **** 0001",
  transaction_count: 120,
  first_date: "2025-01-03",
  last_date: "2026-10-10",
  latest_balance: "1234.50",
  last_import_at: "2026-10-12T09:30:00",
  ...overrides,
});

const record = (id: number, overrides: Partial<ImportRecord> = {}): ImportRecord => ({
  id,
  filename: `export-${id}.csv`,
  imported_at: `2026-10-${String(10 + id).padStart(2, "0")}T09:30:00`,
  row_count: 12,
  inserted_count: 10,
  duplicate_count: id,
  transaction_count: 40 + id,
  masked_iban: "BE00 **** 0001",
  account_label: "KBC",
  ...overrides,
});

const TODAY = new Date(2026, 9, 18);
const accounts = [
  account(1, { imports: [record(2), record(1)] }),
  account(2, {
    imports: [record(3, { filename: "card.pdf" })],
    last_import_at: "2026-10-15T08:00:00", bank: "Belfius", kind: "card", label: "Belfius card", masked: "5555 **** 1234", latest_balance: null, last_date: "2026-08-01" }),
  account(3, { account_type: "kids", imports: [], bank: "Belfius", masked: "BE00 **** 0003", transaction_count: 0, first_date: null, last_date: null, latest_balance: null, last_import_at: null }),
];

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => (url.endsWith("/statements") ? [] : accounts));
});
afterEach(cleanup);

const renderView = (flow: Partial<ImportFlow> = {}) => {
  const onClose = vi.fn();
  const full = {
    busy: false, previews: [], undoImport: vi.fn(async () => {}), previewFiles: vi.fn(),
    importTypeOf: () => "personal", setImportType: vi.fn(), cancelPreview: vi.fn(), commitImport: vi.fn(), ...flow,
  } as unknown as ImportFlow;
  render(<AccountsView flow={full} onClose={onClose} today={TODAY} />);
  return { onClose, flow: full };
};

const detailsPane = () => screen.getByRole("complementary");

describe("AccountsView", () => {
  it("lists accounts under a header row per entity and skips empty entities", async () => {
    renderView();
    expect(await screen.findByText("KBC · account")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Accounts and cards\s*3/ })).toBeInTheDocument();
    expect(screen.getByText(/Every imported account and card/)).toBeInTheDocument();
    expect(screen.getByText(/Belfius card statement PDFs/)).toBeInTheDocument();
    expect(screen.getByText("Import files")).toBeInTheDocument();
    const headers = screen.getAllByRole("columnheader", { name: /^(Personal|Business|Kids)$/ }).map((cell) => cell.textContent);
    expect(headers).toEqual(["Personal", "Kids"]);
    const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent?.slice(0, 8))).toEqual(["Personal", "KBC · ac", "Belfius ", "Kids", "Belfius "]);
    expect(rows[1].textContent).toContain("BE00 **** 0001");
    expect(rows[1].textContent).toContain("120");
    expect(rows[1].textContent).toContain("2025-01-03");
    expect(rows[1].textContent).toContain("2026-10-10");
    expect(rows[1].textContent).toContain("€ 1.234,50");
    expect(rows[1].textContent).toContain(new Date("2026-10-12T09:30:00").toLocaleString());
    expect(rows[2].textContent).toContain("Belfius · card");
    expect(rows[2].textContent).toContain("—");
    expect(rows[4].textContent).not.toContain("days ago");
  });

  it("warns when the last transaction is more than 35 days before today", async () => {
    renderView();
    await screen.findByText("KBC · account");
    const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row");
    expect(rows[2].textContent).not.toContain("days ago"); // 8 days
    expect(rows[3].textContent).toContain("78 days ago");
    expect(daysSince("2026-09-13", TODAY)).toBe(35);
    expect(daysSince("2026-09-12", TODAY)).toBe(36);
  });

  it("selects rows with the arrow keys and closes with the close button", async () => {
    const { onClose } = renderView();
    await screen.findByText("KBC · account");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    const selected = document.querySelectorAll(".selected-rule");
    expect(selected).toHaveLength(1);
    expect(selected[0].textContent).toContain("Belfius");
    expect(selected[0].textContent).toContain("BE00 **** 0003");
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(document.querySelector(".selected-rule")?.textContent).toContain("Belfius · card");
    fireEvent.click(screen.getByRole("button", { name: "Close accounts" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("preselects the most recently imported account and shows its imports, newest first", async () => {
    renderView();
    await screen.findByText("KBC · account");
    expect(document.querySelector(".selected-rule")?.textContent).toContain("Belfius · card");
    await waitFor(() => expect(detailsPane()).toHaveAccessibleName("Details of Belfius card"));
    expect(detailsPane().textContent).toContain("5555 **** 1234");
    // A card's import that brought no statement still gets a row with its Undo import.
    expect(await within(detailsPane()).findByRole("button", { name: "Undo import card.pdf" })).toHaveAttribute(
      "title", `card.pdf, imported ${new Date("2026-10-13T09:30:00").toLocaleString()}`,
    );
    expect(detailsPane().textContent).toContain("no new statements (3 duplicates skipped)");
  });

  it("changes the details when selecting by click or arrow, and says when there are no imports", async () => {
    renderView();
    await screen.findByText("KBC · account");
    fireEvent.click(screen.getByText("KBC · account"));
    const names = () => [...detailsPane().querySelectorAll(".account-import-file")].map((item) => item.textContent);
    expect(names()).toEqual(["export-2.csv", "export-1.csv"]);
    expect(detailsPane()).toHaveAccessibleName("Details of KBC");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await within(detailsPane()).findByRole("button", { name: "Undo import card.pdf" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(detailsPane().textContent).toContain("BE00 **** 0003");
    expect(detailsPane().textContent).toContain("No imports.");
    expect(screen.queryByRole("button", { name: "Undo import" })).not.toBeInTheDocument();
  });

  it("calls the undo flow for an import and then reloads the accounts", async () => {
    const { flow } = renderView();
    await screen.findByText("KBC · account");
    fireEvent.click(screen.getByText("KBC · account"));
    const callsBefore = apiMock.mock.calls.length;
    fireEvent.click(screen.getAllByRole("button", { name: "Undo import" })[1]);
    expect(flow.undoImport).toHaveBeenCalledWith(accounts[0].imports![1]);
    await waitFor(() => expect(apiMock.mock.calls.length).toBe(callsBefore + 1));
    expect(apiMock).toHaveBeenLastCalledWith("/api/accounts/overview");
  });

  it("copes with a server that sends no imports", async () => {
    apiMock.mockImplementation(async () => [account(1, { imports: undefined })]);
    renderView();
    await screen.findByText("KBC · account");
    expect(detailsPane().textContent).toContain("No imports.");
  });

  it("shows the import preview above the table when previews are staged", async () => {
    const preview = {
      token: "t", filename: "new.csv", masked_iban: "BE00 **** 0001", account_label: "KBC", account_type: "personal",
      account_type_required: false, transaction_count: 3, duplicate_count: 0, new_count: 3, unknown_columns: [],
      preview: [], sample_truncated: false,
    } as ImportPreview;
    renderView({ previews: [preview] });
    await screen.findByText("KBC · account");
    const heading = screen.getByRole("heading", { name: "new.csv" });
    const table = screen.getAllByRole("table")[0];
    expect(heading.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeInTheDocument();
  });

  it("shows an error instead of crashing when the accounts cannot be loaded", async () => {
    apiMock.mockImplementation(async () => { throw new Error("404"); });
    renderView();
    expect(await screen.findByText("Could not load the accounts")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Accounts and cards\s*0/ })).toBeInTheDocument();
  });

  it("lists a selected card's statements beside the imports that brought them", async () => {
    const statement = (id: number, status: string, importId: number, extra = {}) => ({
      id, period: `2026-0${id}-07`, debit_date: `2026-0${id}-16`, import_id: importId, purchase_count: id, removed_count: 0,
      total: "-10.00", carried_credit: null, due: "10.00", status, settlement: null, ...extra,
    });
    const card = {
      ...accounts[1],
      imports: [record(7, { filename: "march.pdf" }), record(6, { filename: "earlier.csv" }), record(5, { filename: "empty.csv" })],
    };
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/accounts/2/statements") {
        return [
          statement(4, "unpaid", 7), statement(3, "nothing_due", 6, { due: "0.00" }), statement(2, "credit", 6, { total: "68.25", due: "0.00" }),
          statement(1, "paid", 6, { settlement: { id: 9, booking_date: "2026-01-16", amount: "-10.00", account: "Belfius •••• 1234" } }),
        ];
      }
      return [accounts[0], card, accounts[2]];
    });
    renderView();
    await screen.findByText("KBC · account");
    // The card was imported most recently, so it starts selected; bank accounts never ask for statements.
    expect(apiMock).not.toHaveBeenCalledWith("/api/accounts/1/statements");
    const table = await within(detailsPane()).findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1).map((row) => [...row.querySelectorAll("td")].map((cell) => cell.textContent));
    expect(rows).toEqual([
      ["2026-04-164 purchases", "€ 10,00", "No payment found", "Undo"],
      ["2026-03-163 purchases", "€ 0,00", "Nothing to pay", "Undo"],
      ["2026-02-162 purchases", "+€ 68,25", "Credit carried over"],
      ["2026-01-161 purchase", "€ 10,00", "Paidfrom Belfius •••• 1234"],
      [`${new Date(card.imports[2].imported_at).toLocaleDateString()} · no new statements (5 duplicates skipped)`, "Undo"],
    ]);
    // One Undo import spans the three statements of earlier.csv.
    expect(within(table).getByRole("button", { name: "Undo import earlier.csv" }).closest("td")).toHaveAttribute("rowspan", "3");
    expect(within(table).getByText("Paid", { exact: false }).closest("td")).toHaveAttribute("title", "Paid on 2026-01-16 from Belfius •••• 1234");
    expect(within(table).getByText("No payment found")).toHaveClass("statement-unpaid");
    expect(screen.getByRole("heading", { name: "Statements and imports" })).toBeInTheDocument();
  });
});
