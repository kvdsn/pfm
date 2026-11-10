import React from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CategoryCellEditor, type CategoryChoice } from "./CategoryCellEditor";
import { isAmountQuery } from "./amountSearch";
import { clearOnEscape } from "./hooks";
import type { Annotation, FollowUp, FollowUpFilter, SelectedCategory, SettlementStatement, Transaction } from "./types";
import { api } from "./api";
import { money, shortDate } from "./format";
import type { SortKey, TransactionSort } from "./transactionSort";

/** Own-account moves and itemized card payments have no category to set, so they cannot be selected. */
export const isSelectable = (transaction: Transaction) => !transaction.own_transfer && !transaction.itemized;

/** Shown above the table while transactions are selected: set one category on all of them, or clear. */
export function SelectionBar({
  count,
  choices,
  chosen,
  picking,
  disabled,
  onChosen,
  onPicking,
  onApply,
  onClear,
}: {
  count: number;
  choices: CategoryChoice[];
  /** The category picked for the batch, and whether its picker is open; both live in the parent so Enter can drive them. */
  chosen: CategoryChoice | null;
  picking: boolean;
  disabled: boolean;
  onChosen: (choice: CategoryChoice | null) => void;
  onPicking: (picking: boolean) => void;
  onApply: (categoryId: number, categoryName: string) => void;
  onClear: () => void;
}) {
  const setChosen = onChosen;
  const setPicking = onPicking;
  return (
    <div className="selection-bar" role="group" aria-label="Selected transactions">
      <strong>{count} selected</strong>
      <div className="selection-picker">
        {picking ? (
          <CategoryCellEditor
            choices={choices}
            currentCategoryId={chosen?.id ?? null}
            suggestedCategoryId={null}
            disabled={disabled}
            onChoose={(categoryId) => {
              setChosen(choices.find((choice) => choice.id === categoryId) ?? null);
              setPicking(false);
            }}
            onClose={() => setPicking(false)}
          />
        ) : (
          <button
            className="triage-category-button"
            aria-label="Category for the selected transactions"
            disabled={disabled}
            onClick={() => setPicking(true)}
          >
            {chosen ? <span>{chosen.name}</span> : <span className="muted">Choose category…</span>}
          </button>
        )}
      </div>
      <button
        className="button secondary"
        disabled={disabled || chosen?.id == null}
        onClick={() => chosen?.id != null && onApply(chosen.id, chosen.name)}
      >Set category</button>
      <button className="button secondary" disabled={disabled} onClick={onClear}>Clear</button>
    </div>
  );
}

function UncountedTag({ transaction }: { transaction: Transaction }) {
  return transaction.own_transfer ? (
    <span className="uncounted-tag" title="Moved between your own accounts; not counted as income or expenses">
      ⇄ Own account
    </span>
  ) : (
    <span className="uncounted-tag" title="Card settlement; its statement's purchases are counted instead">
      Itemized card payment
    </span>
  );
}

/** How a transaction's category was set: the deciding rule's text, by hand, or by default. */
export function CategorySource({
  transaction,
  onOpenRule,
}: {
  transaction: Transaction;
  onOpenRule: (ruleId: number) => void;
}) {
  if (transaction.category_source === "manual") {
    return <td className="transaction-source" title="Set by hand">Hand</td>;
  }
  if (transaction.category_source === "rule" && transaction.category_rule) {
    const title = `Categorized by rule “${transaction.category_rule}”`;
    const { category_rule_id: ruleId, category_rule_position: position } = transaction;
    // A deleted rule has no position to open.
    if (ruleId === null || position === null) {
      return <td className="transaction-source" title={title}>Rule</td>;
    }
    return (
      <td className="transaction-source" title={title}>
        <button
          className="rule-link"
          aria-label={`Open rule #${position}, ${transaction.category_rule}`}
          onClick={(event) => {
            event.stopPropagation();
            onOpenRule(ruleId);
          }}
        >
          Rule #{position}
        </button>
      </td>
    );
  }
  return <td className="transaction-source" title="No rule matched">—</td>;
}

/** What the transactions table shows when the search or category filter leaves nothing, with a way out. */
function EmptyTransactions({
  search,
  year,
  category,
  onClearSearch,
  onClearCategory,
  followUpFilter,
}: {
  followUpFilter: FollowUpFilter;
  search: string;
  year: string;
  category: string | null;
  onClearSearch: () => void;
  onClearCategory: () => void;
}) {
  const kind = followUpFilter === "open" ? "open follow-ups" : "done follow-ups";
  const title = followUpFilter !== "all"
    ? <>{search ? <>No {kind} match “{search}”</> : <>No {kind}</>}</>
    : search
    ? <>No transactions match “{search}”{category && <> in {category}</>} in {year}</>
    : category ? <>No {category} transactions in {year}</> : <>No transactions in {year}</>;
  return (
    <div className="transactions-empty" role="status">
      <p className="transactions-empty-title">{title}</p>
      <p className="muted small">Try another search, category, or year.</p>
      {(search || category) && (
        <div className="transactions-empty-actions">
          {search && <button className="button secondary" onClick={onClearSearch}>Clear search</button>}
          {category && <button className="button secondary" onClick={onClearCategory}>Show all categories</button>}
        </div>
      )}
    </div>
  );
}

/** The bank's original texts for the selected transaction, in the details dock. */
function OriginalTexts({ transaction }: { transaction: Transaction }) {
  const fields: [string, string][] = [
    ["Description", transaction.description],
    ["Memo", transaction.memo],
    ["Counterparty", [transaction.counterparty, transaction.counterparty_iban].filter(Boolean).join(" · ")],
  ];
  return (
    // Every field shows, empty or not, so the dock keeps its height from one transaction to the next.
    <dl className="original-texts">
      {fields.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value || "—"}</dd></div>
      ))}
    </dl>
  );
}

/** The Transaction cell: the merchant and, after a dot, the details, on one line that is cut with an ellipsis. */
function TransactionSummary({ transaction, marker = false }: { transaction: Transaction; marker?: boolean }) {
  const merchant = transaction.merchant || "—";
  return (
    <td className="transaction-summary" title={transaction.details ? `${merchant} · ${transaction.details}` : merchant}>
      <span className="transaction-line">
        <span className="transaction-merchant">
          {merchant}
          {marker && <AnnotationMarker transaction={transaction} />}
        </span>
        {transaction.details && <span className="transaction-details">{" · "}{transaction.details}</span>}
      </span>
    </td>
  );
}

const STATEMENT_PREVIEW = 10;

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/**
 * The statement behind an itemized card settlement, as rows of the transactions table under it:
 * a heading, its purchases with the table's own columns, and how they add up to the payment.
 */
function StatementRows({ transactionId, columns, selecting, withYear, onOpenRule }: {
  transactionId: number;
  columns: number;
  selecting: boolean;
  /** Whether dates show their year, as when the list spans all years. */
  withYear: boolean;
  onOpenRule: (ruleId: number) => void;
}) {
  const [statement, setStatement] = useState<SettlementStatement | null>(null);
  const [failed, setFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    let current = true;
    setStatement(null);
    setFailed(false);
    setShowAll(false);
    api<SettlementStatement>(`/api/transactions/${transactionId}/statement`)
      .then((result) => { if (current) setStatement(result); })
      .catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [transactionId]);
  if (!statement) {
    return (
      <tr className="statement-heading-row">
        <td colSpan={columns}>{failed ? "The statement could not be loaded." : "Loading the statement…"}</td>
      </tr>
    );
  }
  const purchases = showAll ? statement.purchases : statement.purchases.slice(0, STATEMENT_PREVIEW);
  const hidden = statement.purchases.length - purchases.length;
  // The label spans the columns before Amount; the columns after it stay empty.
  const labelColumns = selecting ? 4 : 3;
  const sumRow = (label: string, amount: string, className = "statement-sum-row") => (
    <tr className={className} key={label}>
      <td colSpan={labelColumns}>{label}</td>
      <td className={`transaction-amount ${Number(amount) < 0 ? "negative" : "positive"}`}>{money(amount)}</td>
      <td colSpan={2} />
    </tr>
  );
  return (
    <>
      <tr className="statement-heading-row">
        <td colSpan={columns}>
          Itemized on {statement.card} · statement debited {statement.debit_date} · {plural(statement.purchase_count, "purchase", "purchases")}
        </td>
      </tr>
      {purchases.map((purchase) => (
        <tr className="statement-purchase-row" key={purchase.id}>
          {selecting && <td className="select-cell" />}
          <td className="transaction-date" title={purchase.booking_date}>{shortDate(purchase.booking_date, withYear)}</td>
          <td className="transaction-account">{purchase.account}</td>
          <TransactionSummary transaction={purchase} />
          <td className={`transaction-amount ${Number(purchase.amount) < 0 ? "negative" : "positive"}`}>{money(purchase.amount)}</td>
          <td className="transaction-category">
            <span className={`statement-category${!purchase.category || purchase.category === "Uncategorized" ? " uncategorized-label" : ""}`}>{purchase.category || "Uncategorized"}</span>
          </td>
          <CategorySource transaction={purchase} onOpenRule={onOpenRule} />
        </tr>
      ))}
      {hidden > 0 && (
        <tr className="statement-more-row">
          <td colSpan={columns}>
            <button className="rule-link" onClick={() => setShowAll(true)}>
              Show all {statement.purchases.length} purchases
            </button>
          </td>
        </tr>
      )}
      {sumRow(
        statement.removed_count
          ? `Total, including ${plural(statement.removed_count, "removed purchase", "removed purchases")}`
          : "Total",
        statement.total,
      )}
      {statement.carried_credit && sumRow("Credit from the previous statement", statement.carried_credit)}
      {statement.carried_credit && sumRow("Paid", `-${statement.due}`)}
    </>
  );
}

/** The longest note the server accepts. */
export const NOTE_MAX_LENGTH = 500;

/** What a row's marker says when the transaction has no note text to show. */
const FOLLOW_UP_LABELS: Record<FollowUp, string> = { open: "Open follow-up", done: "Follow-up done" };

/** A small mark in the Transaction cell for a note or follow-up; its tooltip shows the note. */
function AnnotationMarker({ transaction }: { transaction: Transaction }) {
  const { follow_up: followUp, note } = transaction;
  if (!followUp && !note) return null;
  const label = note || (followUp ? FOLLOW_UP_LABELS[followUp] : "");
  const kind = followUp ?? "note";
  return (
    <span className={`annotation-marker ${kind}`} title={label} role="img" aria-label={`${followUp ? FOLLOW_UP_LABELS[followUp] : "Note"}: ${label}`}>
      {kind === "open" ? "●" : kind === "done" ? "✓" : "✎"}
    </span>
  );
}

/** The selected transaction's note field, shown when it has a note or one is being added. */
function NoteEditor({
  transaction,
  noteInput,
  onAnnotate,
  onLeave,
  onClose,
}: {
  transaction: Transaction;
  noteInput: React.RefObject<HTMLInputElement>;
  onAnnotate: (transaction: Transaction, change: Partial<Pick<Annotation, "note" | "follow_up">>) => Promise<boolean>;
  /** Returns focus to the transaction's row. */
  onLeave: () => void;
  /** Hides the field again when it is left empty. */
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(transaction.note);
  // The last value sent or saved, so Enter followed by blur does not save twice.
  const sent = useRef(transaction.note);
  const skipBlur = useRef(false);
  useEffect(() => {
    setDraft(transaction.note);
    sent.current = transaction.note;
  }, [transaction.id, transaction.note]);

  const save = () => {
    const value = draft.trim();
    if (!value) onClose();
    if (value === sent.current) {
      setDraft(value);
      return;
    }
    const previous = sent.current;
    sent.current = value;
    setDraft(value);
    void onAnnotate(transaction, { note: value }).then((saved) => {
      if (!saved) sent.current = previous;
    });
  };

  return (
    <div className="annotation-editor">
      <label className="annotation-note">
        <span>Note</span>
        <input
          ref={noteInput}
          className="note-input"
          value={draft}
          maxLength={NOTE_MAX_LENGTH}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            if (skipBlur.current) skipBlur.current = false;
            else save();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              save();
              // Back to the row, like after choosing a category, so the shortcuts work again.
              onLeave();
            } else if (event.key === "Escape") {
              // Reverts the field only; the global Escape (unselect, clear filter) must not follow.
              event.preventDefault();
              event.stopPropagation();
              event.nativeEvent.stopImmediatePropagation();
              skipBlur.current = true;
              setDraft(transaction.note);
              sent.current = transaction.note;
              if (!transaction.note) onClose();
              onLeave();
              skipBlur.current = false;
            }
          }}
        />
      </label>
    </div>
  );
}

/**
 * The selected transaction's details, stuck to the bottom of the viewport so that moving through
 * the rows never shifts them: the bank's texts, the note, and for an itemized card payment a way to
 * list its purchases under its row.
 */
function TransactionDock({
  transaction,
  noteOpen,
  noteInput,
  onAnnotate,
  onOpenNote,
  onLeave,
  onCloseNote,
  statementOpen,
  onToggleStatement,
}: {
  transaction: Transaction;
  noteOpen: boolean;
  noteInput: React.RefObject<HTMLInputElement>;
  onAnnotate: (transaction: Transaction, change: Partial<Pick<Annotation, "note" | "follow_up">>) => Promise<boolean>;
  onOpenNote: (transaction: Transaction) => void;
  onLeave: () => void;
  onCloseNote: () => void;
  statementOpen: boolean;
  onToggleStatement: () => void;
}) {
  return (
    <div className="transaction-dock" role="region" aria-label="Transaction details">
      <OriginalTexts transaction={transaction} />
      <TransactionActions transaction={transaction} onAnnotate={onAnnotate} onOpenNote={onOpenNote} onLeave={onLeave} />
      {(transaction.note || noteOpen) && (
        <NoteEditor
          transaction={transaction}
          noteInput={noteInput}
          onAnnotate={onAnnotate}
          onLeave={onLeave}
          onClose={onCloseNote}
        />
      )}
      {transaction.itemized && (
        <button className="rule-link transaction-dock-statement" aria-expanded={statementOpen} onClick={onToggleStatement}>
          {statementOpen ? "Hide the statement's purchases" : "Show the statement's purchases"}
        </button>
      )}
    </div>
  );
}

/** Note and follow-up actions for the selected transaction, on the first line of the details dock. */
function TransactionActions({
  transaction,
  onAnnotate,
  onOpenNote,
  onLeave,
}: {
  transaction: Transaction;
  onAnnotate: (transaction: Transaction, change: Partial<Pick<Annotation, "note" | "follow_up">>) => Promise<boolean>;
  onOpenNote: (transaction: Transaction) => void;
  onLeave: () => void;
}) {
  const followUp = transaction.follow_up;
  const annotate = (change: Partial<Pick<Annotation, "note" | "follow_up">>) => {
    void onAnnotate(transaction, change);
    onLeave();
  };
  return (
    <div className="transaction-actions" role="group" aria-label="Selected transaction">
      <button className="button secondary" onClick={() => onOpenNote(transaction)}>
        {transaction.note ? "Edit note" : "Add note"}
      </button>
      <label className="annotation-follow-up">
        <input
          type="checkbox"
          checked={Boolean(followUp)}
          onChange={(event) => annotate({ follow_up: event.target.checked ? "open" : null })}
        />
        <span>Follow up</span>
      </label>
      {followUp === "open" && (
        <button className="button secondary" onClick={() => annotate({ follow_up: "done" })}>Done</button>
      )}
      {followUp === "done" && (
        <>
          <span className="muted">Done on {transaction.follow_up_at?.slice(0, 10) ?? "—"}</span>
          <button className="button secondary" onClick={() => annotate({ follow_up: "open" })}>Reopen</button>
        </>
      )}
    </div>
  );
}

const FOLLOW_UP_FILTERS: [FollowUpFilter, string][] = [["all", "All"], ["open", "Follow-ups"], ["done", "Done"]];

/** All transactions of the year, or the open or done follow-ups of every year. */
function FollowUpFilterControl({
  value,
  openCount,
  onChange,
}: {
  value: FollowUpFilter;
  /** The entity's open follow-ups; unknown when the server does not report it. */
  openCount: number | null;
  onChange: (value: FollowUpFilter) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label="Show transactions">
      {FOLLOW_UP_FILTERS.map(([mode, label]) => (
        <button
          key={mode}
          className={value === mode ? "on" : ""}
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
        >
          {label}
          {mode === "open" && openCount ? <> <span className="segmented-count">{openCount}</span></> : null}
        </button>
      ))}
    </div>
  );
}

/** A column header that sorts the list by its column, showing the direction while it does. */
function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort: TransactionSort | null;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const direction = sort?.key === sortKey ? sort.direction : null;
  return (
    <th className={className} aria-sort={direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"}>
      <button type="button" className={direction ? "sort-header sorted" : "sort-header"} onClick={() => onSort(sortKey)}>
        {label}
        <span className="sort-arrow" aria-hidden="true">{direction === "asc" ? "↑" : direction === "desc" ? "↓" : "↕"}</span>
      </button>
    </th>
  );
}

export function TransactionsPanel({
  year,
  transactions,
  total,
  sort,
  onSort,
  search,
  onSearch,
  searchInput,
  selectedCategory,
  onClearCategory,
  activeTransactionId,
  onActivate,
  onRowClick,
  rangeIds,
  onEditCategory,
  isUncategorized,
  rowRefs,
  onRuleFromSearch,
  onOpenRule,
  selectedIds,
  onToggleSelected,
  onSelectAll,
  selectionBar,
  categoryEditor,
  ruleOffer,
  followUpFilter,
  openFollowUps,
  onFollowUpFilter,
  onAnnotate,
  noteInput,
  noteOpenId,
  onOpenNote,
  onCloseNote,
}: {
  year: string;
  transactions: Transaction[];
  total: number;
  /** The order shown in the Date and Amount headers; null when neither column orders the list. */
  sort: TransactionSort | null;
  onSort: (key: SortKey) => void;
  search: string;
  onSearch: (value: string) => void;
  searchInput: React.RefObject<HTMLInputElement>;
  selectedCategory: SelectedCategory | null;
  onClearCategory: () => void;
  activeTransactionId: number | null;
  onActivate: (transactionId: number) => void;
  /** A click on a row; unlike focus, it also ends a Shift+arrow range. */
  onRowClick: (transactionId: number) => void;
  /** Transactions in the Shift+arrow range, highlighted; empty when there is none. */
  rangeIds: Set<number>;
  onEditCategory: (transaction: Transaction) => void;
  isUncategorized: (transaction: Transaction) => boolean;
  rowRefs: React.MutableRefObject<Map<number, HTMLTableRowElement>>;
  onRuleFromSearch: () => void;
  /** Opens a rule, by id, in the rules view. */
  onOpenRule: (ruleId: number) => void;
  /** Transactions ticked for a bulk category change; the checkboxes show while searching or once something is selected. */
  selectedIds: Set<number>;
  onToggleSelected: (transactionId: number) => void;
  /** Selects every selectable listed transaction, or clears the selection. */
  onSelectAll: (select: boolean) => void;
  /** The bar for acting on the selection, shown above the table. */
  selectionBar: React.ReactNode;
  /** The inline category editor, shown in its transaction's category cell. */
  categoryEditor: { transactionId: number; element: React.ReactNode } | null;
  /** An offer shown under a transaction, such as making a rule after a category change. */
  ruleOffer: { transactionId: number; element: React.ReactNode } | null;
  followUpFilter: FollowUpFilter;
  /** The entity's open follow-ups, or null when the server does not report them. */
  openFollowUps: number | null;
  onFollowUpFilter: (value: FollowUpFilter) => void;
  /** Saves a note or follow-up change; resolves to whether it was saved. */
  onAnnotate: (transaction: Transaction, change: Partial<Pick<Annotation, "note" | "follow_up">>) => Promise<boolean>;
  /** The selected transaction's note field, so the M shortcut can focus it. */
  noteInput: React.RefObject<HTMLInputElement>;
  /** The transaction whose empty note field is open for typing; a note that has text always shows. */
  noteOpenId: number | null;
  /** Shows the transaction's note field and focuses it. */
  onOpenNote: (transaction: Transaction) => void;
  onCloseNote: () => void;
}) {
  // The checkbox column shows while searching, and otherwise only once something is selected.
  const selecting = search.trim() !== "" || selectedIds.size > 0;
  const selectable = transactions.filter(isSelectable);
  const selectedCount = selectable.filter((transaction) => selectedIds.has(transaction.id)).length;
  const allSelected = selectable.length > 0 && selectedCount === selectable.length;
  const selectAll = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectAll.current) selectAll.current.indeterminate = selectedCount > 0 && !allSelected;
  });
  const columns = selecting ? 7 : 6;
  const activeTransaction = transactions.find((transaction) => transaction.id === activeTransactionId);
  const focusRow = (transactionId: number) => rowRefs.current.get(transactionId)?.focus();
  // The itemized card payment whose purchases are listed under it; it stays open until closed or another one opens.
  const [statementOpenId, setStatementOpenId] = useState<number | null>(null);
  // The dock covers the bottom of the viewport, so rows scrolled into view keep clear of it. Its height
  // follows the selected transaction (a note adds a line), so the row scrolls again once the dock has it.
  const panel = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const dock = panel.current?.querySelector<HTMLElement>(".transaction-dock-row");
    if (!dock || activeTransactionId === null) return;
    panel.current?.style.setProperty("--dock-height", `${dock.offsetHeight}px`);
    rowRefs.current.get(activeTransactionId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeTransactionId, noteOpenId, activeTransaction?.note]);
  return (
    <section className="panel transactions-panel" ref={panel}>
      <div className="section-heading transactions-heading">
        <div><h3>Transactions{followUpFilter !== "all" && " · all years"} <span className="count">{total}</span></h3></div>
        <div className="transaction-filters">
          <FollowUpFilterControl value={followUpFilter} openCount={openFollowUps} onChange={onFollowUpFilter} />
          {selectedCategory && (
            <button className="active-filter" onClick={onClearCategory}>
              {selectedCategory.flow_type === "own_transfer"
                ? selectedCategory.name
                : `${selectedCategory.name} · ${selectedCategory.flow_type}`} ×
            </button>
          )}
          <input
            ref={searchInput}
            className="search-input"
            aria-label="Search transactions"
            placeholder="Search merchant, description, memo, or amount…"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            onKeyDown={clearOnEscape(() => onSearch(""))}
          />
          {search.trim() && !isAmountQuery(search) && (
            <button className="button secondary" onClick={onRuleFromSearch}>Make rule</button>
          )}
        </div>
      </div>
      {total > transactions.length && (
        <p className="muted small">Showing {transactions.length} of {total} matches. Refine your search to narrow the list.</p>
      )}
      {selecting && selectionBar}
      {transactions.length ? (
        <div className="table-scroll transactions-scroll">
          <table className="transactions-table">
            <thead>
              <tr>
                {selecting && (
                  <th className="select-cell">
                    <input
                      ref={selectAll}
                      type="checkbox"
                      aria-label="Select all listed transactions"
                      checked={allSelected}
                      disabled={selectable.length === 0}
                      onChange={() => onSelectAll(!allSelected)}
                    />
                  </th>
                )}
                <SortHeader label="Date" sortKey="date" sort={sort} onSort={onSort} />
                <th className="account-header">Account</th><th>Transaction</th>
                <SortHeader label="Amount" sortKey="amount" sort={sort} onSort={onSort} className="amount-header" />
                <th>Category</th><th>Set by</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((transaction) => {
                const category = transaction.category || "Uncategorized";
                const uncounted = transaction.own_transfer || transaction.itemized;
                return (
                  <React.Fragment key={transaction.id}>
                    <tr
                      className={[
                        activeTransactionId === transaction.id ? "active-transaction-row" : "",
                        rangeIds.has(transaction.id) ? "in-range" : "",
                        uncounted ? "uncounted-row" : "",
                      ].join(" ").trim()}
                      tabIndex={0}
                      ref={(element) => {
                        if (element) rowRefs.current.set(transaction.id, element);
                        else rowRefs.current.delete(transaction.id);
                      }}
                      onFocus={(event) => {
                        // Ticking a checkbox is not selecting the row.
                        if (!(event.target as HTMLElement).closest(".select-cell")) onActivate(transaction.id);
                      }}
                      onClick={() => onRowClick(transaction.id)}
                      onKeyDown={(event) => {
                        // While rows are ticked, Enter belongs to the bulk category bar (handled in App).
                        if (event.key === "Enter" && event.target === event.currentTarget && !uncounted && !selectedIds.size) {
                          event.preventDefault();
                          onEditCategory(transaction);
                        }
                      }}
                    >
                      {selecting && (
                        <td className="select-cell" onClick={(event) => event.stopPropagation()}>
                          {!uncounted && (
                            <input
                              type="checkbox"
                              aria-label={`Select ${transaction.merchant || "—"} ${transaction.booking_date}`}
                              checked={selectedIds.has(transaction.id)}
                              onChange={() => onToggleSelected(transaction.id)}
                            />
                          )}
                        </td>
                      )}
                      <td className="transaction-date" title={transaction.booking_date}>{shortDate(transaction.booking_date, followUpFilter !== "all")}</td>
                      <td className="transaction-account">{transaction.account}</td>
                      <TransactionSummary transaction={transaction} marker />
                      <td className={`transaction-amount ${Number(transaction.amount) < 0 ? "negative" : "positive"}`}>
                        {money(transaction.amount)}
                      </td>
                      <td className="transaction-category">
                        {categoryEditor?.transactionId === transaction.id ? categoryEditor.element
                          : uncounted ? <UncountedTag transaction={transaction} /> : (
                          <button
                            className={`transaction-category-label${isUncategorized(transaction) ? " uncategorized-label" : ""}`}
                            aria-label={`Change category: ${category}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              onEditCategory(transaction);
                            }}
                          >
                            {category}
                          </button>
                        )}
                      </td>
                      <CategorySource transaction={transaction} onOpenRule={onOpenRule} />
                    </tr>
                    {statementOpenId === transaction.id && transaction.itemized && (
                      <StatementRows transactionId={transaction.id} columns={columns} selecting={selecting} withYear={followUpFilter !== "all"} onOpenRule={onOpenRule} />
                    )}
                    {ruleOffer?.transactionId === transaction.id && (
                      <tr className="rule-offer-row">
                        <td colSpan={columns}>{ruleOffer.element}</td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
            {activeTransaction && (
              <tfoot>
                <tr className="transaction-dock-row">
                  <td colSpan={columns}>
                    <TransactionDock
                      transaction={activeTransaction}
                      noteOpen={noteOpenId === activeTransaction.id}
                      noteInput={noteInput}
                      onAnnotate={onAnnotate}
                      onOpenNote={onOpenNote}
                      onLeave={() => focusRow(activeTransaction.id)}
                      onCloseNote={onCloseNote}
                      statementOpen={statementOpenId === activeTransaction.id}
                      onToggleStatement={() => setStatementOpenId(statementOpenId === activeTransaction.id ? null : activeTransaction.id)}
                    />
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      ) : (
        <EmptyTransactions
          followUpFilter={followUpFilter}
          search={search.trim()}
          year={year}
          category={selectedCategory?.name ?? null}
          onClearSearch={() => onSearch("")}
          onClearCategory={onClearCategory}
        />
      )}
    </section>
  );
}
