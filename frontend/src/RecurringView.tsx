import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ACCOUNT_TYPE_LABELS,
  type AccountType,
  type Category,
  type LinkableRecurringItem,
  type RecurringCadence,
  type RecurringFlag,
  type RecurringItem,
  type RecurringList,
} from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";
import { isEditableTarget, useArrowSelection, useSignal } from "./hooks";

const CADENCES: RecurringCadence[] = ["monthly", "quarterly", "yearly"];
const FLAG_LABELS: Record<RecurringFlag, string> = { new: "New", price_change: "Price changed", stopped: "Stopped" };

type Section = "items" | "candidates" | "ignored";
const itemKey = (item: RecurringItem) => `${item.merchant}\u0000${item.kind}`;
const label = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

/** Thirteen small bars, one per month: green up for money in, red down for money out, a dot for a month without payments. */
function HistoryStrip({ item }: { item: RecurringItem }) {
  const peak = Math.max(0, ...item.history.map((month) => Math.abs(Number(month.amount ?? 0))));
  return (
    <span className="recurring-history" role="img" aria-label={`Last 13 months of ${item.merchant}`}>
      {item.history.map((month) => {
        const value = month.amount === null ? null : Number(month.amount);
        const height = value === null || peak === 0 ? 0 : Math.max(2, Math.round((Math.abs(value) / peak) * 18));
        return (
          <span
            key={month.period}
            className={`recurring-bar ${value === null ? "empty" : value < 0 ? "negative-bar" : "positive-bar"}`}
            title={`${month.period}: ${value === null ? "no payment" : money(month.amount)}`}
          >
            {value !== null && <span style={{ height }} />}
          </span>
        );
      })}
    </span>
  );
}

function FlagTags({ flags }: { flags: RecurringFlag[] }) {
  return (
    <>
      {flags.map((flag) => <span key={flag} className={`recurring-flag flag-${flag}`}>{FLAG_LABELS[flag]}</span>)}
    </>
  );
}

type Draft = {
  cadence: RecurringCadence;
  day: string;
  categoryId: string;
  amounts: { date: string; amount: string }[];
  start: string;
  end: string;
  note: string;
  linkId: string;
};

const draftOf = (item: RecurringItem): Draft => ({
  cadence: item.cadence,
  day: item.day_of_month === null ? "" : String(item.day_of_month),
  categoryId: item.category_id === null ? "" : String(item.category_id),
  amounts: item.amounts.map((entry) => ({ date: entry.effective_date, amount: entry.amount })),
  start: item.start_date,
  end: item.end_date ?? "",
  note: item.note,
  linkId: item.linked ? String(item.linked.id) : "",
});

function ItemFacts({ item }: { item: RecurringItem }) {
  return (
    <dl className="recurring-facts">
      <div><dt>Amount</dt><dd className={Number(item.amount) < 0 ? "negative" : "positive"}>{money(item.amount)} {item.cadence}</dd></div>
      <div><dt>Per month</dt><dd>{money(item.monthly_amount)}</dd></div>
      <div><dt>Last payment</dt><dd>{item.last_date ?? "—"}</dd></div>
      <div><dt>Next payment</dt><dd>{item.next_date ?? "—"}</dd></div>
    </dl>
  );
}

/** The editable form for a confirmed item. */
function ConfirmedForm({
  item, categories, linkable, busy, error, onSave, onIgnore, onRemove,
}: {
  item: RecurringItem;
  categories: Category[];
  linkable: LinkableRecurringItem[];
  busy: boolean;
  error: string;
  onSave: (draft: Draft) => void;
  onIgnore: () => void;
  onRemove: () => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(item));
  const change = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const setAmount = (index: number, patch: Partial<Draft["amounts"][number]>) =>
    change({ amounts: draft.amounts.map((entry, position) => (position === index ? { ...entry, ...patch } : entry)) });
  return (
    <form className="recurring-form" onSubmit={(event) => { event.preventDefault(); onSave(draft); }}>
      <ItemFacts item={item} />
      <HistoryStrip item={item} />
      <label>Cadence
        <select value={draft.cadence} onChange={(event) => change({ cadence: event.target.value as RecurringCadence })}>
          {CADENCES.map((cadence) => <option key={cadence} value={cadence}>{label(cadence)}</option>)}
        </select>
      </label>
      <label>Day of month
        <input type="number" min={1} max={31} value={draft.day} onChange={(event) => change({ day: event.target.value })} />
      </label>
      <label>Category
        <select value={draft.categoryId} onChange={(event) => change({ categoryId: event.target.value })}>
          <option value="">No category</option>
          {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select>
      </label>
      <fieldset className="recurring-amounts">
        <legend>Amount schedule</legend>
        {draft.amounts.map((entry, index) => (
          <div className="recurring-amount-row" key={index}>
            <input type="date" aria-label={`Effective date ${index + 1}`} value={entry.date} onChange={(event) => setAmount(index, { date: event.target.value })} />
            <input type="text" inputMode="decimal" aria-label={`Amount ${index + 1}`} value={entry.amount} onChange={(event) => setAmount(index, { amount: event.target.value })} />
            <button
              type="button"
              className="icon-button"
              aria-label={`Remove amount ${index + 1}`}
              disabled={draft.amounts.length < 2}
              onClick={() => change({ amounts: draft.amounts.filter((_, position) => position !== index) })}
            >×</button>
          </div>
        ))}
        <button
          type="button"
          className="button secondary"
          onClick={() => change({ amounts: [...draft.amounts, { date: "", amount: draft.amounts[draft.amounts.length - 1]?.amount ?? "" }] })}
        >Add amount change</button>
      </fieldset>
      <div className="recurring-dates">
        <label>Start date
          <input type="date" value={draft.start} onChange={(event) => change({ start: event.target.value })} />
        </label>
        <label>End date
          <input type="date" value={draft.end} onChange={(event) => change({ end: event.target.value })} />
        </label>
      </div>
      <label>Note
        <textarea rows={2} value={draft.note} onChange={(event) => change({ note: event.target.value })} />
      </label>
      <label>Linked to
        <select value={draft.linkId} onChange={(event) => change({ linkId: event.target.value })}>
          <option value="">Not linked</option>
          {linkable.map((other) => (
            <option key={other.id} value={other.id}>
              {ACCOUNT_TYPE_LABELS[other.account_type]} · {other.merchant} · {other.kind} · {money(other.amount)} {other.cadence}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="recurring-error" role="alert">{error}</p>}
      <div className="impact-actions recurring-actions">
        <button type="submit" className="button primary" disabled={busy}>Save</button>
        <button type="button" className="button secondary" disabled={busy} onClick={onIgnore}>Ignore</button>
        <button type="button" className="button secondary" disabled={busy} onClick={onRemove}>Remove</button>
      </div>
    </form>
  );
}

/** Today as YYYY-MM-DD in local time, the default start of a payment added by hand. */
const localToday = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};

/** A merchant and kind with recent transactions and no recurring payment yet, which an added payment can follow. */
type TransactionGroup = {
  merchant: string; kind: string; count: number; first_date: string; last_date: string;
  last_amount: string; category_id: number | null; category: string | null;
};
const groupLabel = (group: TransactionGroup) =>
  `${[group.merchant, group.kind].filter(Boolean).join(" · ")} (${group.count} ${group.count === 1 ? "payment" : "payments"}, last ${group.last_date}, ${money(group.last_amount)})`;

type NewPayment = {
  /** The transactions it follows, or null for a payment without any yet. */
  group: TransactionGroup | null;
  name: string;
  cadence: RecurringCadence;
  amount: string;
  day: string;
  start: string;
  end: string;
  categoryId: string;
  note: string;
};

/** The form for a payment added by hand, such as one that varies too much to be suggested. */
function AddForm({
  accountType, categories, busy, error, onAdd, onCancel,
}: {
  accountType: AccountType;
  categories: Category[];
  busy: boolean;
  error: string;
  onAdd: (payment: NewPayment) => void;
  onCancel: () => void;
}) {
  const [payment, setPayment] = useState<NewPayment>(() => ({
    group: null, name: "", cadence: "monthly", amount: "", day: "", start: localToday(), end: "", categoryId: "", note: "",
  }));
  const change = (patch: Partial<NewPayment>) => setPayment((current) => ({ ...current, ...patch }));
  const [groups, setGroups] = useState<TransactionGroup[]>([]);
  const [groupText, setGroupText] = useState("");
  useEffect(() => {
    let current = true;
    api<TransactionGroup[]>(`/api/recurring/groups?account_type=${accountType}`)
      .then((found) => { if (current) setGroups(Array.isArray(found) ? found : []); })
      .catch(() => { if (current) setGroups([]); });
    return () => { current = false; };
  }, [accountType]);
  /** Choosing a group fills in its name, latest amount, category, day and first date; all stay editable but the name. */
  const chooseGroup = (text: string) => {
    setGroupText(text);
    const group = groups.find((candidate) => groupLabel(candidate) === text) ?? null;
    if (!group) {
      if (payment.group) change({ group: null });
      return;
    }
    change({
      group, name: group.merchant, amount: group.last_amount, start: group.first_date,
      day: String(Number(group.last_date.slice(8, 10))), categoryId: group.category_id === null ? "" : String(group.category_id),
    });
  };
  const follows = useRef<HTMLInputElement>(null);
  useEffect(() => follows.current?.focus(), []);
  return (
    <form className="recurring-form" onSubmit={(event) => { event.preventDefault(); onAdd(payment); }}>
      <p className="muted small">Use a negative amount for money out, such as -400 for a monthly top-up.</p>
      <label>Follows transactions of
        <input
          ref={follows}
          type="text"
          list="recurring-groups"
          placeholder="Type a merchant, or leave empty"
          value={groupText}
          onChange={(event) => chooseGroup(event.target.value)}
        />
        <datalist id="recurring-groups">
          {groups.map((group) => <option key={`${group.merchant}\u0000${group.kind}`} value={groupLabel(group)} />)}
        </datalist>
        <span className="muted">Its payments then set the next date, and leave the category's usual monthly amount, so nothing counts twice.</span>
      </label>
      <label>Name
        <input type="text" value={payment.name} disabled={payment.group !== null} onChange={(event) => change({ name: event.target.value })} />
      </label>
      <label>Cadence
        <select value={payment.cadence} onChange={(event) => change({ cadence: event.target.value as RecurringCadence })}>
          {CADENCES.map((cadence) => <option key={cadence} value={cadence}>{label(cadence)}</option>)}
        </select>
      </label>
      <label>Amount
        <input type="text" inputMode="decimal" value={payment.amount} onChange={(event) => change({ amount: event.target.value })} />
      </label>
      <div className="recurring-dates">
        <label>Start date
          <input type="date" value={payment.start} onChange={(event) => change({ start: event.target.value })} />
        </label>
        <label>End date
          <input type="date" value={payment.end} onChange={(event) => change({ end: event.target.value })} />
        </label>
      </div>
      <label>Day of month
        <input type="number" min={1} max={31} placeholder="The start date's day" value={payment.day} onChange={(event) => change({ day: event.target.value })} />
      </label>
      <label>Category
        <select value={payment.categoryId} onChange={(event) => change({ categoryId: event.target.value })}>
          <option value="">No category</option>
          {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select>
      </label>
      <label>Note
        <textarea rows={2} value={payment.note} onChange={(event) => change({ note: event.target.value })} />
      </label>
      {error && <p className="recurring-error" role="alert">{error}</p>}
      <div className="impact-actions recurring-actions">
        <button type="submit" className="button primary" disabled={busy}>Add payment</button>
        <button type="button" className="button secondary" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** The full-page recurring payments view: confirmed items, detected suggestions and ignored ones. */
export function RecurringView({
  accountType,
  categories,
  onNotice,
  onError,
  onClose,
  onChanged,
  newItemSignal = 0,
  active = true,
}: {
  /** Whether the arrow keys and Enter act on the list; off while the Forecast page's other sections are in use. */
  active?: boolean;
  accountType: AccountType;
  categories: Category[];
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  /** Omitted when the list is a section of the Forecast page, which has its own close button. */
  onClose?: () => void;
  /** Called after a change, since confirmed payments feed the forecast. */
  onChanged?: () => void;
  /** Bumped by N: opens the form to add a payment by hand. */
  newItemSignal?: number;
}) {
  const [list, setList] = useState<RecurringList | null>(null);
  const [linkable, setLinkable] = useState<LinkableRecurringItem[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showIgnored, setShowIgnored] = useState(false);
  const [busy, setBusy] = useState(false);
  const [paneError, setPaneError] = useState("");
  const [adding, setAdding] = useState(false);

  const base = `/api/recurring`;
  const query = `account_type=${accountType}`;

  const load = useCallback(async () => {
    try {
      const [fresh, others] = await Promise.all([
        api<RecurringList>(`/api/recurring?account_type=${accountType}`),
        api<LinkableRecurringItem[]>(`/api/recurring/linkable?account_type=${accountType}`).catch(() => []),
      ]);
      setList(fresh);
      setLinkable(others);
    } catch (cause) {
      onError(errorMessage(cause, "Could not load recurring payments"));
    }
  }, [accountType, onError]);
  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(() => {
    const sections: { section: Section; item: RecurringItem }[] = [];
    if (list) {
      for (const item of list.items) sections.push({ section: "items", item });
      for (const item of list.candidates) sections.push({ section: "candidates", item });
      if (showIgnored) for (const item of list.ignored) sections.push({ section: "ignored", item });
    }
    return sections;
  }, [list, showIgnored]);
  const keys = rows.map((row) => itemKey(row.item));
  const selected = rows.find((row) => itemKey(row.item) === selectedKey) ?? null;

  const pane = useRef<HTMLElement>(null);
  const select = (key: string | null) => {
    setSelectedKey(key);
    setAdding(false);
    setPaneError("");
  };
  const startAdding = () => {
    setSelectedKey(null);
    setPaneError("");
    setAdding(true);
    revealPane.current = true;
  };
  useSignal(newItemSignal, startAdding);
  // A clicked row: below 1240px the pane sits under the list, so it is brought into view once rendered.
  const revealPane = useRef(false);
  const showPane = () => {
    if (window.matchMedia?.("(max-width: 1240px)").matches) pane.current?.scrollIntoView?.({ block: "nearest" });
  };
  const choose = (key: string) => {
    if (key === selectedKey) return showPane();
    revealPane.current = true;
    select(key);
  };
  useEffect(() => {
    if (!revealPane.current) return;
    revealPane.current = false;
    showPane();
  }, [selectedKey, adding]);
  useArrowSelection(keys, selectedKey, (key) => {
    select(key);
    requestAnimationFrame(() => {
      document.querySelector(`[data-recurring-key="${CSS.escape(key)}"]`)?.scrollIntoView?.({ block: "nearest" });
    });
  }, active);

  /** Runs a change, reloads the list and keeps the selection on the same merchant and kind where it still exists. */
  const run = async (action: () => Promise<void>, failure: string) => {
    setBusy(true);
    setPaneError("");
    onError("");
    try {
      await action();
      await load();
      onChanged?.();
    } catch (cause) {
      setPaneError(errorMessage(cause, failure));
    } finally {
      setBusy(false);
    }
  };

  const decide = (item: RecurringItem, status: "confirmed" | "ignored") =>
    run(async () => {
      await api(base, { method: "POST", json: { account_type: accountType, merchant: item.merchant, kind: item.kind, status } });
      onNotice(`${status === "confirmed" ? "Confirmed" : "Ignored"} ${item.merchant}.`);
    }, `Could not ${status === "confirmed" ? "confirm" : "ignore"} ${item.merchant}`);

  const patch = (item: RecurringItem, body: Record<string, unknown>, failure: string, notice?: string) =>
    run(async () => {
      await api(`${base}/${item.id}`, { method: "PATCH", json: { account_type: accountType, ...body } });
      if (notice) onNotice(notice);
    }, failure);

  const remove = (item: RecurringItem, notice: string) =>
    run(async () => {
      await api(`${base}/${item.id}?${query}`, { method: "DELETE" });
      onNotice(notice);
    }, `Could not remove ${item.merchant}`);

  const add = (payment: NewPayment) =>
    run(async () => {
      const created = await api<RecurringItem>(`${base}/manual`, {
        method: "POST",
        json: {
          account_type: accountType,
          merchant: payment.group?.merchant ?? payment.name,
          kind: payment.group?.kind ?? "",
          cadence: payment.cadence,
          amount: payment.amount,
          start_date: payment.start,
          end_date: payment.end === "" ? null : payment.end,
          day_of_month: payment.day === "" ? null : Number(payment.day),
          category_id: payment.categoryId === "" ? null : Number(payment.categoryId),
          note: payment.note,
        },
      });
      setAdding(false);
      setSelectedKey(itemKey(created));
      onNotice(`Added ${created.merchant}.`);
    }, "Could not add the payment");

  const save = (item: RecurringItem, draft: Draft) =>
    run(async () => {
      const original = draftOf(item);
      await api(`${base}/${item.id}`, {
        method: "PATCH",
        json: {
          account_type: accountType,
          cadence: draft.cadence,
          day_of_month: draft.day === "" ? null : Number(draft.day),
          category_id: draft.categoryId === "" ? null : Number(draft.categoryId),
          start_date: draft.start,
          end_date: draft.end === "" ? null : draft.end,
          note: draft.note,
          amounts: draft.amounts.map((entry) => ({ effective_date: entry.date, amount: entry.amount })),
        },
      });
      if (draft.linkId !== original.linkId) {
        await api(`${base}/${item.id}/link`, {
          method: "PUT",
          json: { account_type: accountType, linked_item_id: draft.linkId === "" ? null : Number(draft.linkId) },
        });
      }
      onNotice(`Saved ${item.merchant}.`);
    }, `Could not save ${item.merchant}`);

  // Enter confirms the selected suggestion, unless a control has focus and takes the key itself.
  useEffect(() => {
    const confirm = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (!active || !selected || selected.section !== "candidates" || busy || isEditableTarget(event.target)) return;
      if (event.target instanceof HTMLElement && event.target.closest("button")) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      event.preventDefault();
      void decide(selected.item, "confirmed");
    };
    window.addEventListener("keydown", confirm);
    return () => window.removeEventListener("keydown", confirm);
  });

  const tableFor = (section: Section, items: RecurringItem[]) => (
    <div className="table-scroll">
      <table className="rules-table recurring-table" aria-label={section === "items" ? "Confirmed" : section === "candidates" ? "Suggested" : "Ignored"}>
        {/* The same fixed columns in every section, so the three tables line up. */}
        <colgroup>
          <col /><col className="recurring-col-cadence" /><col className="recurring-col-amount" /><col className="recurring-col-next" />
          <col className="recurring-col-category" /><col className="recurring-col-history" /><col className="recurring-col-actions" />
        </colgroup>
        <thead>
          <tr>
            <th>Payment</th><th>Cadence</th><th className="numeric">Amount</th><th>Next</th><th>Category</th><th>13 months</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const key = itemKey(item);
            return (
              <tr
                key={key}
                data-recurring-key={key}
                className={key === selectedKey ? "selected-rule" : ""}
                aria-selected={key === selectedKey}
                onClick={() => choose(key)}
              >
                <td className="recurring-payment">
                  <div className="recurring-merchant" title={item.merchant}><strong>{item.merchant}</strong></div>
                  <div className="muted recurring-kind" title={item.kind}>{item.kind}</div>
                  {item.flags.length > 0 && <div className="recurring-flags"><FlagTags flags={item.flags} /></div>}
                </td>
                <td className="nowrap">{label(item.cadence)}</td>
                <td className={`numeric recurring-amount ${Number(item.amount) < 0 ? "negative" : "positive"}`}>{money(item.amount)}</td>
                <td className="nowrap recurring-date">{item.next_date ?? "—"}</td>
                <td className="recurring-category" title={item.category ?? ""}>{item.category ?? <span className="muted">—</span>}</td>
                <td><HistoryStrip item={item} /></td>
                {section === "candidates" && (
                  <td className="recurring-row-actions">
                    <div className="triage-buttons">
                      <button className="button secondary" disabled={busy} aria-label={`Confirm ${item.merchant}`} onClick={(event) => { event.stopPropagation(); void decide(item, "confirmed"); }}>Confirm</button>
                      <button className="button secondary" disabled={busy} aria-label={`Ignore ${item.merchant}`} onClick={(event) => { event.stopPropagation(); void decide(item, "ignored"); }}>Ignore</button>
                    </div>
                  </td>
                )}
                {section === "items" && <td className="recurring-row-actions" />}
                {section === "ignored" && (
                  <td className="recurring-row-actions">
                    <div className="triage-buttons">
                      <button className="button secondary" disabled={busy} aria-label={`Restore ${item.merchant}`} onClick={(event) => { event.stopPropagation(); void remove(item, `Restored ${item.merchant}.`); }}>Restore</button>
                    </div>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const summary = list?.monthly;
  const item = selected?.item ?? null;

  return (
    <div className={selected || adding ? "rules-view" : "rules-view single-pane"}>
      <section className="panel rules-pane" aria-label="Recurring payments">
        <div className="section-heading">
          <div><h3>Recurring payments</h3></div>
          <div className="transaction-filters">
            {onClose && <button className="icon-button" aria-label="Close recurring payments" onClick={onClose}>×</button>}
          </div>
        </div>
        {summary && (
          <p className="recurring-summary" aria-label="Monthly total of confirmed payments">
            <span>Income <strong className="positive">{money(summary.income)}</strong></span>
            <span>Expenses <strong className="negative">{money(summary.expense)}</strong></span>
            <span>Net <strong className={Number(summary.net) < 0 ? "negative" : "positive"}>{money(summary.net)}</strong></span>
            <span className="muted">a month, confirmed payments</span>
          </p>
        )}
        {list && (
          <>
            <div className="recurring-heading recurring-heading-row">
              <h4>Confirmed <span className="count">{list.items.length}</span></h4>
              <button className="button secondary" onClick={startAdding}>Add payment</button>
            </div>
            {list.items.length > 0 ? tableFor("items", list.items) : <p className="muted small">Nothing confirmed yet. Confirm a suggestion below.</p>}
            <h4 className="recurring-heading">Suggested <span className="count">{list.candidates.length}</span></h4>
            {list.candidates.length > 0 ? tableFor("candidates", list.candidates) : <p className="muted small">No new recurring payments found.</p>}
            {list.ignored.length > 0 && (
              <>
                <h4 className="recurring-heading">
                  <button className="recurring-toggle" aria-expanded={showIgnored} onClick={() => setShowIgnored((shown) => !shown)}>
                    {showIgnored ? "Hide ignored" : `Show ${list.ignored.length} ignored`}
                  </button>
                </h4>
                {showIgnored && tableFor("ignored", list.ignored)}
              </>
            )}
          </>
        )}
      </section>
      {adding && (
        <aside ref={pane} className="panel impact-pane recurring-pane" aria-label="Add a recurring payment">
          <div className="impact-heading">
            <div>
              <h3>Add payment</h3>
              <div className="muted small">A recurring payment that isn't suggested, such as a top-up that varies</div>
            </div>
          </div>
          <AddForm accountType={accountType} categories={categories} busy={busy} error={paneError} onAdd={(payment) => void add(payment)} onCancel={() => setAdding(false)} />
        </aside>
      )}
      {!adding && selected && item && (
        <aside ref={pane} className="panel impact-pane recurring-pane" aria-label="Recurring payment details">
          <div className="impact-heading">
            <div>
              <h3>{item.merchant}</h3>
              <div className="muted small">
                {[item.kind, selected.section === "items" ? "Confirmed" : selected.section === "candidates" ? "Suggested" : "Ignored"].filter(Boolean).join(" · ")}
              </div>
            </div>
          </div>
          {selected.section === "items" ? (
            <ConfirmedForm
              key={JSON.stringify(item)}
              item={item}
              categories={categories}
              linkable={linkable}
              busy={busy}
              error={paneError}
              onSave={(draft) => void save(item, draft)}
              onIgnore={() => void patch(item, { status: "ignored" }, `Could not ignore ${item.merchant}`, `Ignored ${item.merchant}.`)}
              onRemove={() => void remove(item, `Removed ${item.merchant}; it can come back as a suggestion.`)}
            />
          ) : (
            <div className="recurring-form">
              <ItemFacts item={item} />
              <HistoryStrip item={item} />
              <dl className="recurring-facts">
                <div><dt>Cadence</dt><dd>{label(item.cadence)}</dd></div>
                <div><dt>Day of month</dt><dd>{item.day_of_month ?? "—"}</dd></div>
                <div><dt>Category</dt><dd>{item.category ?? "—"}</dd></div>
                <div><dt>Since</dt><dd>{item.start_date}</dd></div>
                <div><dt>Payments</dt><dd>{item.occurrences} in the last 400 days</dd></div>
              </dl>
              {item.flags.length > 0 && <div className="recurring-flags"><FlagTags flags={item.flags} /></div>}
              <div>
                <div className="muted small">Amount schedule</div>
                <ul className="recurring-schedule">
                  {item.amounts.map((entry) => (
                    <li key={entry.effective_date}><span>{entry.effective_date}</span><span className={Number(entry.amount) < 0 ? "negative" : "positive"}>{money(entry.amount)}</span></li>
                  ))}
                </ul>
              </div>
              {paneError && <p className="recurring-error" role="alert">{paneError}</p>}
              <div className="impact-actions recurring-actions">
                {selected.section === "candidates" ? (
                  <>
                    <button className="button primary" disabled={busy} onClick={() => void decide(item, "confirmed")}>Confirm</button>
                    <button className="button secondary" disabled={busy} onClick={() => void decide(item, "ignored")}>Ignore</button>
                  </>
                ) : (
                  <button className="button secondary" disabled={busy} onClick={() => void remove(item, `Restored ${item.merchant}.`)}>Restore</button>
                )}
              </div>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
