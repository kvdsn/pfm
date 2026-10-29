import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AccountType, Budget, ManagedCategory, Transaction } from "./types";
import { api, errorMessage } from "./api";
import { euroNumber, exactMoney, money, overviewNumber } from "./format";
import { clearOnEscape, useArrowSelection, useSignal } from "./hooks";
import { FIELDS, MATCH_MODE_LABELS } from "./ruleFields";

const UNCATEGORIZED = "Uncategorized";
const LATEST_TRANSACTIONS = 30;
const FIELD_LABELS = Object.fromEntries(FIELDS);
/** A budget as people type it: "2.000" or "1.500,50". */
const budgetText = (amount: string) => (Number(amount) % 1 === 0 ? overviewNumber : euroNumber).format(Number(amount));
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** One category as a row: an inline name, its counts, and merge and remove actions. */
function CategoryRow({
  category,
  targets,
  selected,
  busy,
  year,
  budget,
  onSelect,
  onRename,
  onToggleIncome,
  onBudget,
  onMerge,
  onRemove,
}: {
  category: ManagedCategory;
  targets: ManagedCategory[];
  selected: boolean;
  busy: boolean;
  /** The year budgets are for; empty when there is no data yet, which hides the budget column. */
  year: string;
  budget: Budget | undefined;
  onSelect: () => void;
  onRename: (name: string) => void;
  onToggleIncome: (isIncome: boolean) => void;
  /** Sets the budget from the typed text, which the server parses; empty removes it. */
  onBudget: (text: string) => void;
  onMerge: (target: ManagedCategory) => void;
  onRemove: () => void;
}) {
  const [name, setName] = useState(category.name);
  useEffect(() => setName(category.name), [category.name]);
  const protectedCategory = category.name === UNCATEGORIZED;
  const budgetShown = budget ? budgetText(budget.amount) : "";
  /** What is being typed in the budget field; null shows the saved budget. */
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const saveBudget = () => {
    const text = (budgetDraft ?? budgetShown).trim();
    setBudgetDraft(null);
    if (text !== budgetShown) onBudget(text);
  };
  const save = () => {
    const trimmed = name.trim();
    if (!trimmed) setName(category.name);
    else if (trimmed !== category.name) onRename(trimmed);
  };
  return (
    <tr data-category-id={category.id} className={selected ? "selected-category" : ""} onClick={onSelect} onFocus={onSelect}>
      <td className="category-name-cell">
        {protectedCategory ? (
          // Plain text, not a disabled field: browsers drop clicks on disabled fields, so the row could not be selected.
          <span className="protected-category-name" title="Protected system category">{category.name}</span>
        ) : (
        <input
          aria-label={`Name of category ${category.name}`}
          value={name}
          maxLength={80}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
            if (event.key === "Escape") {
              setName(category.name);
              event.stopPropagation();
            }
          }}
        />
        )}
      </td>
      <td className="category-income-cell">
        <input
          type="checkbox"
          aria-label={`Count ${category.name} as income`}
          title={protectedCategory ? "Uncategorized counts by the sign of each amount" : "Count this category as income instead of expense"}
          checked={category.is_income === true}
          disabled={protectedCategory || busy}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => onToggleIncome(event.target.checked)}
        />
      </td>
      {year && (
        <td className="category-budget-cell">
          <input
            inputMode="decimal"
            aria-label={`Budget ${year} for ${category.name}`}
            placeholder="—"
            title={protectedCategory ? "Uncategorized cannot have a budget" : undefined}
            value={budgetDraft ?? budgetShown}
            maxLength={14}
            disabled={protectedCategory || busy}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setBudgetDraft(event.target.value)}
            onBlur={saveBudget}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") {
                setBudgetDraft(null);
                event.stopPropagation();
              }
            }}
          />
        </td>
      )}
      <td className="numeric">{category.rules.length}</td>
      <td className="numeric">{category.transaction_count}</td>
      <td className="category-actions">
        {!protectedCategory && (
          <>
            <select
              aria-label={`Merge ${category.name} into`}
              value=""
              disabled={busy}
              onChange={(event) => {
                const target = targets.find((item) => item.id === Number(event.target.value));
                if (target) onMerge(target);
              }}
            >
              <option value="">Merge into…</option>
              {targets.filter((item) => item.id !== category.id).map((item) => (
                <option value={item.id} key={item.id}>{item.name}</option>
              ))}
            </select>
            <button
              className="icon-button delete-rule"
              aria-label={`Remove category ${category.name}`}
              title="Remove category"
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onRemove();
              }}
            >×</button>
          </>
        )}
      </td>
    </tr>
  );
}

/** What remains of a budget, or how far it is exceeded, in words. */
function budgetLeft(category: ManagedCategory, budget: Budget, used: number) {
  const left = Math.round((Number(budget.amount) - used) * 100) / 100;
  if (left >= 0) return `${exactMoney(left)} ${category.is_income ? "to go" : "left"}`;
  return `${exactMoney(-left)} ${category.is_income ? "above target" : "over"}`;
}

/** The selected category's rules and latest transactions. */
function CategoryDetails({
  accountType,
  category,
  year,
  budget,
  used,
  onEditRules,
}: {
  accountType: AccountType;
  category: ManagedCategory;
  year: string;
  budget: Budget | undefined;
  /** The category's total in the year, net of refunds. */
  used: number;
  onEditRules: (category: ManagedCategory) => void;
}) {
  const [transactions, setTransactions] = useState<Transaction[] | null>(null);
  useEffect(() => {
    setTransactions(null);
    const filter = category.name === UNCATEGORIZED ? "uncategorized" : String(category.id);
    api<{ items: Transaction[] }>(
      `/api/transactions?account_type=${accountType}&category_id=${filter}&limit=${LATEST_TRANSACTIONS}`,
    )
      .then((page) => setTransactions(page.items))
      .catch(() => setTransactions([]));
  }, [accountType, category.id, category.name, category.transaction_count]);

  return (
    <aside className="panel impact-pane category-details" aria-label={`Details of ${category.name}`}>
      <div className="impact-heading">
        <h3>{category.name}</h3>
        {category.name !== UNCATEGORIZED && (
          <button className="button secondary" onClick={() => onEditRules(category)}>Edit rules</button>
        )}
      </div>
      <p className="impact-summary">
        {plural(category.rules.length, "rule")} · {plural(category.transaction_count, "transaction")}
      </p>
      {budget && (
        <p className="category-budget-summary">
          {category.is_income ? "Target" : "Budget"} {year}: {exactMoney(Number(budget.amount))}
          {" · "}{category.is_income ? "received" : "used"} {exactMoney(used)} ({Math.round((used / Number(budget.amount)) * 100)}%)
          {" · "}{budgetLeft(category, budget, used)}
        </p>
      )}
      {category.rules.length > 0 && (
        <ul className="category-rule-list">
          {category.rules.map((rule) => (
            <li key={rule.id}>
              <span className="muted">{MATCH_MODE_LABELS[rule.match_mode]}</span> {rule.match_text}
              <span className="muted"> · {rule.match_fields.map((field) => FIELD_LABELS[field]).join(", ")}</span>
              {rule.flow_type !== "expense" && <span className="muted"> · {rule.flow_type}</span>}
              <span className="muted category-rule-wins"> {rule.first_match_count} / {rule.match_count}</span>
              {(rule.itemized_count ?? 0) > 0 && (
                <span className="health-tag category-rule-tag" title={`${rule.itemized_count} of ${rule.match_count} matches are card payments replaced by their statement's purchases; they keep this category but don't count in totals`}>{rule.itemized_count} itemized</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <h4 className="category-details-subheading">Latest transactions</h4>
      <div className="impact-matches">
        {transactions === null && <p className="muted">Loading…</p>}
        {transactions?.map((transaction) => (
          <div className="category-transaction" key={transaction.id}>
            <span className="muted">{transaction.booking_date}</span>
            <span className="category-transaction-merchant">
              {transaction.merchant || "—"}
              {transaction.details && <span className="muted"> · {transaction.details}</span>}
            </span>
            <span className={Number(transaction.amount) < 0 ? "negative" : "positive"}>{money(transaction.amount)}</span>
          </div>
        ))}
        {transactions?.length === 0 && <p className="muted">No transactions.</p>}
      </div>
    </aside>
  );
}

/** Every category of an account type, edited inline, with the selected one's details. */
export function CategoriesView({
  accountType,
  year = "",
  budgets = [],
  yearTotals = new Map(),
  onNotice,
  onError,
  onChanged,
  onEditRules,
  onClose,
  newItemSignal = 0,
}: {
  accountType: AccountType;
  /** The selected year, which budgets are for; empty hides the budget column. */
  year?: string;
  /** The entity's budgets for that year. */
  budgets?: Budget[];
  /** Each category's total in that year, net of refunds. */
  yearTotals?: Map<number, number>;
  /** Focuses the new category field each time this counter changes, such as from a keyboard shortcut. */
  newItemSignal?: number;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onChanged: () => Promise<void>;
  onEditRules: (category: ManagedCategory) => void;
  onClose: () => void;
}) {
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [filter, setFilter] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setCategories(await api<ManagedCategory[]>(`/api/category-management?account_type=${accountType}`));
    } catch (cause) {
      onError(errorMessage(cause, "Could not load categories"));
    }
  }, [accountType, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const sorted = useMemo(
    () => [...categories].sort((left, right) =>
      Number(right.name === UNCATEGORIZED) - Number(left.name === UNCATEGORIZED) || left.name.localeCompare(right.name)),
    [categories],
  );
  const visible = sorted.filter((category) => category.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const targets = sorted.filter((category) => category.name !== UNCATEGORIZED);
  const selected = categories.find((category) => category.id === selectedId) ?? visible[0] ?? null;

  const tableBody = useRef<HTMLTableSectionElement>(null);
  const newNameInput = useRef<HTMLInputElement>(null);
  useArrowSelection(visible.map((category) => category.id), selected?.id, (id) => {
    setSelectedId(id);
    tableBody.current?.querySelector(`[data-category-id="${id}"]`)?.scrollIntoView?.({ block: "nearest" });
  });
  useSignal(newItemSignal, () => newNameInput.current?.focus());

  const run = async (fallbackError: string, action: () => Promise<void>) => {
    setBusy(true);
    onError("");
    try {
      await action();
      await Promise.all([load(), onChanged()]);
    } catch (cause) {
      onError(errorMessage(cause, fallbackError));
    } finally {
      setBusy(false);
    }
  };

  const rename = (category: ManagedCategory, name: string) => run("Could not rename category", async () => {
    await api(`/api/categories/${category.id}`, { method: "PATCH", json: { name } });
    onNotice(`Renamed ${category.name} to ${name}.`);
  });

  const setIncome = (category: ManagedCategory, isIncome: boolean) => run("Could not change how the category counts", async () => {
    await api(`/api/categories/${category.id}`, { method: "PATCH", json: { is_income: isIncome } });
    onNotice(`${category.name} now counts as ${isIncome ? "income" : "expense"}.`);
  });

  const setBudget = (category: ManagedCategory, text: string) => run("Could not set the budget", async () => {
    const saved = await api<{ amount: string | null }>("/api/budgets", {
      method: "PUT",
      json: { account_type: accountType, category_id: category.id, year, amount: text },
    });
    onNotice(
      saved.amount === null
        ? `Budget for ${category.name} in ${year} removed.`
        : `Budget for ${category.name} in ${year} set to ${exactMoney(Number(saved.amount))}.`,
    );
  });

  const merge = (source: ManagedCategory, target: ManagedCategory) => {
    if (!window.confirm(
      `Merge “${source.name}” into “${target.name}”? Its transactions, rules and budgets move to “${target.name}”, and “${source.name}” is permanently removed.`,
    )) return;
    void run("Could not merge categories", async () => {
      await api("/api/categories/merge", {
        method: "POST",
        json: { account_type: accountType, source_id: source.id, target_id: target.id },
      });
      setSelectedId(target.id);
      onNotice(`Merged ${source.name} into ${target.name}.`);
    });
  };

  const remove = (category: ManagedCategory) => {
    if (!window.confirm(
      `Permanently remove “${category.name}”? Its transactions will be moved to Uncategorized, and its rules and budgets will be deleted.`,
    )) return;
    void run("Could not remove category", async () => {
      await api(`/api/categories/${category.id}`, { method: "DELETE" });
      onNotice(`Removed ${category.name}.`);
    });
  };

  const add = () => {
    const name = newName.trim();
    if (!name) return;
    void run("Could not add category", async () => {
      const created = await api<{ id: number }>("/api/categories", {
        method: "POST",
        json: { account_type: accountType, name },
      });
      setNewName("");
      setSelectedId(created.id);
      onNotice(`Added ${name}.`);
    });
  };

  return (
    <div className="rules-view">
      <section className="panel rules-pane" aria-label="Categories">
        <div className="section-heading">
          <div><h3>Categories <span className="count">{categories.length}</span></h3></div>
          <div className="transaction-filters">
            <input
              className="search-input"
              aria-label="Filter categories"
              placeholder="Filter categories…"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              onKeyDown={clearOnEscape(() => setFilter(""))}
            />
            <button className="icon-button" aria-label="Close categories" onClick={onClose}>×</button>
          </div>
        </div>
        <p className="muted small">
          Rename a category in its row; renaming keeps its rules and transactions. Merging moves both, and any budgets, into the target.
          Income categories count their amounts as income; every other category counts as expense, so a refund lowers it.
        </p>
        <div className="table-scroll">
          <table className="rules-table categories-table">
            <thead>
              <tr>
                <th>Category</th><th>Income</th>
                {year && <th className="numeric category-budget-head" title="Yearly budget; for an income category, a target">Budget {year}</th>}
                <th className="numeric">Rules</th><th className="numeric">Transactions</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody ref={tableBody}>
              {visible.map((category) => (
                <CategoryRow
                  key={category.id}
                  category={category}
                  targets={targets}
                  selected={category.id === selected?.id}
                  busy={busy}
                  year={year}
                  budget={budgets.find((item) => item.category_id === category.id)}
                  onSelect={() => setSelectedId(category.id)}
                  onRename={(name) => void rename(category, name)}
                  onToggleIncome={(isIncome) => void setIncome(category, isIncome)}
                  onBudget={(text) => void setBudget(category, text)}
                  onMerge={(target) => merge(category, target)}
                  onRemove={() => remove(category)}
                />
              ))}
              <tr className="new-category-row">
                <td colSpan={year ? 6 : 5}>
                  <input
                    ref={newNameInput}
                    aria-label="New category name"
                    placeholder="+ Add a category"
                    maxLength={80}
                    value={newName}
                    disabled={busy}
                    onChange={(event) => setNewName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") add();
                      if (event.key === "Escape") {
                        setNewName("");
                        event.stopPropagation();
                      }
                    }}
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      {selected && (
        <CategoryDetails
          accountType={accountType}
          category={selected}
          year={year}
          budget={budgets.find((item) => item.category_id === selected.id)}
          used={yearTotals.get(selected.id) ?? 0}
          onEditRules={onEditRules}
        />
      )}
    </div>
  );
}
