import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AccountType, Category, FlowType, UncategorizedGroup } from "./types";
import { api, errorMessage } from "./api";
import { CategoryCellEditor, type CategoryChoice } from "./CategoryCellEditor";
import { money } from "./format";
import { isEditableTarget, useArrowSelection } from "./hooks";

const UNCATEGORIZED = "Uncategorized";
const UNKNOWN_MERCHANT = "Unknown merchant";

const groupKey = (group: UncategorizedGroup) => `${group.merchant}\u0000${group.kind}`;
const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;

/** The full-page triage view: Uncategorized transactions grouped by merchant and kind, categorized a group at a time. */
/** A group's kind, shortened for display: a transfer's account number stays in the tooltip. */
function kindLabel(kind: string) {
  if (/^Transfer from /i.test(kind)) return "Transfer in";
  if (/^Transfer to /i.test(kind)) return "Transfer out";
  return kind;
}

export function TriageView({
  accountType,
  categories,
  onNotice,
  onError,
  onChanged,
  onClose,
}: {
  accountType: AccountType;
  categories: Category[];
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  /** Refreshes the rest of the app after transactions or rules changed. */
  onChanged: () => Promise<void>;
  onClose: () => void;
}) {
  const [groups, setGroups] = useState<UncategorizedGroup[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  /** The category chosen per group; groups not listed use their suggestion. */
  const [chosen, setChosen] = useState<Map<string, number>>(new Map());
  const [pickerKey, setPickerKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tableBody = useRef<HTMLTableSectionElement>(null);

  const choices: CategoryChoice[] = useMemo(
    () => categories.filter((category) => category.name !== UNCATEGORIZED).map(({ id, name }) => ({ id, name })),
    [categories],
  );
  const categoryOf = (group: UncategorizedGroup): Category | undefined => {
    const id = chosen.get(groupKey(group)) ?? group.suggestion?.category_id;
    return categories.find((category) => category.id === id);
  };

  const load = useCallback(async () => {
    try {
      setGroups(await api<UncategorizedGroup[]>(`/api/uncategorized-groups?account_type=${accountType}`));
    } catch (cause) {
      onError(errorMessage(cause, "Could not load uncategorized transactions"));
    }
  }, [accountType, onError]);
  useEffect(() => {
    void load();
  }, [load]);

  const keys = (groups ?? []).map(groupKey);
  // Arrow keys move the selection and show only the selected group's transactions.
  useArrowSelection(keys, selectedKey, (key) => {
    setSelectedKey(key);
    setExpanded(new Set([key]));
    requestAnimationFrame(() => {
      const row = tableBody.current?.querySelector(`[data-group-key="${CSS.escape(key)}"]`);
      // Bring the expanded transactions into view, then make sure the group's own row stays visible.
      row?.nextElementSibling?.scrollIntoView?.({ block: "nearest" });
      row?.scrollIntoView?.({ block: "nearest" });
    });
  });
  // Enter opens the selected group's category picker, unless a control has focus and takes the key itself.
  useEffect(() => {
    const openPicker = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (selectedKey === null || isEditableTarget(event.target)) return;
      if (event.target instanceof HTMLElement && event.target.closest("button")) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      event.preventDefault();
      setPickerKey(selectedKey);
    };
    window.addEventListener("keydown", openPicker);
    return () => window.removeEventListener("keydown", openPicker);
  });

  /** Reloads the groups in place, so the page keeps its scroll position, and selects the next group if the selected one is gone. */
  const reload = async (afterIndex: number) => {
    const fresh = await api<UncategorizedGroup[]>(`/api/uncategorized-groups?account_type=${accountType}`);
    setGroups(fresh);
    setSelectedKey((current) => {
      if (current === null || fresh.some((group) => groupKey(group) === current)) return current;
      const next = fresh[Math.min(afterIndex, fresh.length - 1)];
      return next ? groupKey(next) : null;
    });
    await onChanged();
  };

  const run = async (group: UncategorizedGroup, action: () => Promise<void>, failure: string) => {
    const index = (groups ?? []).findIndex((item) => groupKey(item) === groupKey(group));
    setBusy(true);
    onError("");
    try {
      await action();
      await reload(index);
    } catch (cause) {
      onError(errorMessage(cause, failure));
    } finally {
      setBusy(false);
    }
  };

  const setCategory = (group: UncategorizedGroup, category: Category) =>
    run(group, async () => {
      const result = await api<{ updated: number }>("/api/transactions/bulk-category", {
        method: "POST",
        json: { account_type: accountType, transaction_ids: group.transaction_ids, category_id: category.id },
      });
      onNotice(`Categorized ${plural(result.updated, "transaction")} as ${category.name}.`);
    }, "Could not set the category");

  const makeRule = (group: UncategorizedGroup, category: Category) =>
    run(group, async () => {
      // Without a flow type of its own, a category takes the one the amounts' sign implies.
      const flowType: FlowType = (category.is_income ? "income" : category.rule_flow_type) ?? (Number(group.total) >= 0 ? "income" : "expense");
      const rule = await api<{ id: number }>("/api/category-rules", {
        method: "POST",
        json: {
          account_type: accountType,
          category_id: category.id,
          match_text: group.merchant,
          match_fields: ["merchant"],
          match_mode: "exact",
          flow_type: flowType,
        },
      });
      const result = await api<{ updated: number }>(`/api/category-rules/${rule.id}/run`, {
        method: "POST",
        json: { account_type: accountType },
      });
      onNotice(`Added a rule for “${group.merchant}”; categorized ${plural(result.updated, "transaction")} as ${category.name}.`);
    }, "Could not make the rule");

  const toggle = (key: string) => setExpanded((current) => {
    const next = new Set(current);
    if (!next.delete(key)) next.add(key);
    return next;
  });

  const total = (groups ?? []).reduce((sum, group) => sum + group.count, 0);

  return (
    <div className="rules-view single-pane">
      <section className="panel rules-pane" aria-label="Triage">
        <div className="section-heading">
          <div><h3>Uncategorized <span className="count">{total}</span></h3></div>
          <div className="transaction-filters">
            <button className="icon-button" aria-label="Close triage" onClick={onClose}>×</button>
          </div>
        </div>
        <p className="muted small">
          Grouped by merchant and kind, largest first. Set a category for a whole group, or make a rule that also categorizes future imports.
        </p>
        {groups !== null && groups.length === 0 ? (
          <p className="muted triage-empty">Nothing to triage — every transaction has a category.</p>
        ) : (
          <div className="table-scroll">
            <table className="rules-table triage-table">
              <thead>
                <tr>
                  <th>Merchant</th><th className="numeric">Count</th><th className="numeric">Total</th>
                  <th>Dates</th><th>Category</th><th aria-label="Actions" />
                </tr>
              </thead>
              <tbody ref={tableBody}>
                {(groups ?? []).map((group) => {
                  const key = groupKey(group);
                  const isOpen = expanded.has(key);
                  const category = categoryOf(group);
                  const otherMatches = group.merchant && group.merchant_rule_matches > group.count
                    ? group.merchant_rule_matches - group.count
                    : 0;
                  return [
                    <tr
                      key={key}
                      data-group-key={key}
                      className={key === selectedKey ? "selected-rule" : ""}
                      onClick={(event) => {
                        setSelectedKey(key);
                        if (!(event.target as HTMLElement).closest(".triage-category, .triage-actions")) toggle(key);
                      }}
                    >
                      <td className="triage-merchant-cell">
                        <div className="triage-merchant">
                          <button
                            className="triage-disclosure"
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? "Hide" : "Show"} transactions of ${group.merchant || UNKNOWN_MERCHANT}`}
                          >{isOpen ? "▾" : "▸"}</button>
                          <strong className={group.merchant ? "" : "muted"}>{group.merchant || UNKNOWN_MERCHANT}</strong>
                          <span className="muted" title={group.kind}> · {kindLabel(group.kind)}</span>
                        </div>
                        <div className="muted triage-sample" title={group.sample_details.join("\n")}>
                          {group.sample_details[0]}
                        </div>
                      </td>
                      <td className="numeric">{group.count}</td>
                      <td className={`triage-total ${Number(group.total) < 0 ? "negative" : "positive"}`}>{money(group.total)}</td>
                      <td className="muted triage-dates">
                        {group.first_date === group.last_date ? group.first_date : <>{group.first_date}<br />{group.last_date}</>}
                      </td>
                      <td className="triage-category">
                        {pickerKey === key ? (
                          <CategoryCellEditor
                            key={key}
                            choices={choices}
                            currentCategoryId={category?.id ?? null}
                            suggestedCategoryId={group.suggestion?.category_id ?? null}
                            disabled={busy}
                            onChoose={(categoryId) => {
                              if (categoryId !== null) setChosen((current) => new Map(current).set(key, categoryId));
                              setPickerKey(null);
                            }}
                            onClose={() => setPickerKey(null)}
                          />
                        ) : (
                          <button
                            className="triage-category-button"
                            aria-label={`Category for ${group.merchant || UNKNOWN_MERCHANT} · ${group.kind}`}
                            disabled={busy}
                            onClick={() => setPickerKey(key)}
                          >
                            {category ? <span>{category.name}</span> : <span className="muted">Choose category…</span>}
                            {category && category.id === group.suggestion?.category_id && <span className="suggested-tag">suggested</span>}
                          </button>
                        )}
                      </td>
                      <td className="triage-actions">
                        <div className="triage-buttons">
                          <button className="button secondary" disabled={busy || !category} onClick={() => category && void setCategory(group, category)}>Set category</button>
                          <button
                            className="button secondary"
                            disabled={busy || !category || !group.merchant}
                            title={group.merchant ? undefined : "A rule needs a merchant to match"}
                            onClick={() => category && void makeRule(group, category)}
                          >Make rule</button>
                        </div>
                        {otherMatches > 0 && (
                          <div className="muted triage-warning" title={`A rule for this merchant also categorizes ${plural(otherMatches, "other transaction")}`}>
                            rule also categorizes {plural(otherMatches, "other")}
                          </div>
                        )}
                      </td>
                    </tr>,
                    isOpen && (
                      <tr key={`${key}\u0000open`} className="triage-detail-row">
                        <td colSpan={6}>
                          {group.transactions.map((item) => (
                            <div className="category-transaction triage-transaction" key={item.id}>
                              <span>{item.date}</span>
                              <span className="category-transaction-merchant muted" title={item.details}>{item.details}</span>
                              <span className={Number(item.amount) < 0 ? "negative" : "positive"}>{money(item.amount)}</span>
                            </div>
                          ))}
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
