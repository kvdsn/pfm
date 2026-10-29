import { useCallback, useEffect, useState } from "react";
import type { AccountType, Category, ClaimTransaction, ReimbursementClaim, Reimbursements as ReimbursementsData, Transaction } from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";

const UNCATEGORIZED = "Uncategorized";
const SEARCH_LIMIT = 15;
const DEFAULT_SHARE = "50";

type Mode = "expense" | "amount";
type Draft = {
  mode: Mode;
  search: string;
  results: Transaction[] | null;
  source: Transaction | null;
  share: string;
  date: string;
  description: string;
  amount: string;
  personalCategoryId: string;
  businessCategoryId: string;
  expectedDate: string;
};
const emptyDraft: Draft = {
  mode: "expense", search: "", results: null, source: null, share: DEFAULT_SHARE, date: "", description: "", amount: "",
  personalCategoryId: "", businessCategoryId: "", expectedDate: "",
};

/** The claim's amount in cents for a share of an expense: half up, like the server. */
export const shareCents = (amount: string, share: string): number | null => {
  const value = Number(share);
  if (share.trim() === "" || Number.isNaN(value) || value <= 0 || value > 100) return null;
  const cents = Math.round((Math.abs(Number(amount)) * 100 * value) / 100 + 1e-9);
  return cents > 0 ? cents : null;
};

const categoryChoices = (categories: Category[]) => categories.filter((category) => category.name !== UNCATEGORIZED);
const status = (claim: ReimbursementClaim) =>
  claim.paid ? `Paid ${claim.paid.date}` : `Open, expected ${claim.expected_on}`;

/** What Business owes Personal for business costs paid privately, and the repayments that settled it. */
export function ReimbursementsSection({
  accountType, categories, onChanged, onNotice,
}: {
  accountType: AccountType;
  /** The categories of the entity being planned. */
  categories: Category[];
  onChanged: () => void;
  onNotice: (message: string) => void;
}) {
  const [data, setData] = useState<ReimbursementsData | null>(null);
  const [otherCategories, setOtherCategories] = useState<Category[]>([]);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const personalCategories = categoryChoices(accountType === "personal" ? categories : otherCategories);
  const businessCategories = categoryChoices(accountType === "business" ? categories : otherCategories);

  const load = useCallback(async () => {
    const other = accountType === "personal" ? "business" : "personal";
    try {
      const [next, nextOther] = await Promise.all([
        api<ReimbursementsData>("/api/reimbursements"),
        api<Category[]>(`/api/categories?account_type=${other}`),
      ]);
      setData(Array.isArray(next?.claims) ? next : null);
      setOtherCategories(Array.isArray(nextOther) ? nextOther : []);
    } catch {
      // An older server has no reimbursements; the section then stays empty.
      setData(null);
    }
  }, [accountType]);
  useEffect(() => {
    void load();
  }, [load]);

  const change = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const run = async (action: () => Promise<void>, failure: string) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
      onChanged();
    } catch (cause) {
      setError(errorMessage(cause, failure));
    } finally {
      setBusy(false);
    }
  };

  const search = async () => {
    setSearching(true);
    setError("");
    try {
      const params = new URLSearchParams({ account_type: "personal", q: draft.search.trim(), limit: String(SEARCH_LIMIT) });
      const page = await api<{ items: Transaction[] }>(`/api/transactions?${params}`);
      change({ results: (page.items ?? []).filter((item) => Number(item.amount) < 0) });
    } catch (cause) {
      setError(errorMessage(cause, "Could not search the transactions"));
    } finally {
      setSearching(false);
    }
  };
  const pick = (item: Transaction) =>
    change({ source: item, personalCategoryId: item.category_id !== null && item.category !== UNCATEGORIZED ? String(item.category_id) : "" });

  const fromExpense = draft.mode === "expense";
  const cents = fromExpense && draft.source ? shareCents(draft.source.amount, draft.share) : null;
  const add = () => {
    if (fromExpense && !draft.source) return setError("Pick the expense first.");
    if (!fromExpense && draft.personalCategoryId === "") return setError("Choose the Personal category.");
    const body: Record<string, unknown> = fromExpense
      ? { source_transaction_id: draft.source?.id, share: Number(draft.share) }
      : { date: draft.date, description: draft.description, amount: draft.amount };
    if (draft.personalCategoryId !== "") body.personal_category_id = Number(draft.personalCategoryId);
    if (draft.businessCategoryId !== "") body.business_category_id = Number(draft.businessCategoryId);
    if (draft.expectedDate !== "") body.expected_date = draft.expectedDate;
    void run(async () => {
      await api("/api/reimbursements", { method: "POST", json: body });
      setAdding(false);
      setDraft(emptyDraft);
      onNotice("Added the claim.");
    }, "Could not add the claim");
  };
  const patch = (claim: ReimbursementClaim, changes: Record<string, unknown>, notice: string) =>
    run(async () => {
      await api(`/api/reimbursements/${claim.id}`, { method: "PATCH", json: changes });
      onNotice(notice);
    }, "Could not change the claim");
  const remove = (claim: ReimbursementClaim) =>
    run(async () => {
      await api(`/api/reimbursements/${claim.id}`, { method: "DELETE" });
      onNotice("Removed the claim.");
    }, "Could not remove the claim");

  /** The candidates, those with the claim's amount first. */
  const candidatesFor = (claim: ReimbursementClaim): ClaimTransaction[] => {
    const all = data?.candidates ?? [];
    const matches = (item: ClaimTransaction) => Number(item.amount) === Number(claim.amount);
    return [...all.filter(matches), ...all.filter((item) => !matches(item))];
  };

  const claims = data?.claims ?? [];
  return (
    <section className="panel plan-panel" aria-label="Reimbursements">
      <div className="plan-heading-row">
        <h4 className="plan-heading">Reimbursements <span className="count">{claims.length}</span></h4>
        <button className="button secondary" onClick={() => { setError(""); setAdding(true); }} disabled={adding}>Add claim</button>
      </div>
      <p className="muted small">What Business owes Personal for business costs paid privately; the forecast expects each open claim to be repaid.</p>
      {data && (
        <p className="plan-line" aria-label="Reimbursement totals">
          Business owes Personal <strong>{money(data.open_total)}</strong>
          <span className="muted"> · {money(data.paid_total_this_year)} repaid this year</span>
        </p>
      )}
      {adding && (
        <form className="recurring-form plan-form plan-planned-form" aria-label="Add claim" onSubmit={(event) => { event.preventDefault(); add(); }}>
          <fieldset>
            <legend>Claim</legend>
            <label className="plan-check">
              <input type="radio" name="claim-mode" checked={fromExpense} onChange={() => change({ mode: "expense" })} />
              From an expense
            </label>
            <label className="plan-check">
              <input type="radio" name="claim-mode" checked={!fromExpense} onChange={() => change({ mode: "amount" })} />
              Amount
            </label>
          </fieldset>
          {fromExpense ? (
            <>
              <label>Search Personal expenses
                <input
                  type="text"
                  value={draft.search}
                  placeholder="Merchant or amount"
                  onChange={(event) => change({ search: event.target.value })}
                  onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void search(); } }}
                />
              </label>
              <div className="plan-actions">
                <button type="button" className="button secondary" disabled={searching} onClick={() => void search()}>Search</button>
              </div>
              {draft.results && (draft.results.length > 0 ? (
                <ul className="plan-list claim-results" aria-label="Expenses found">
                  {draft.results.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className={`button secondary${draft.source?.id === item.id ? " active" : ""}`}
                        aria-pressed={draft.source?.id === item.id}
                        onClick={() => pick(item)}
                      >
                        {item.booking_date} · {item.merchant} · {money(item.amount)}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : <p className="muted small">No expenses found.</p>)}
              <label>Share (%)
                <input type="text" inputMode="decimal" value={draft.share} onChange={(event) => change({ share: event.target.value })} />
              </label>
              <p className="plan-line" aria-label="Claim amount">
                <span className="muted">Business owes: </span>
                {cents === null ? "—" : <strong>{money(cents / 100)}</strong>}
              </p>
            </>
          ) : (
            <>
              <label>Date
                <input type="date" value={draft.date} onChange={(event) => change({ date: event.target.value })} />
              </label>
              <label>Description
                <input type="text" value={draft.description} onChange={(event) => change({ description: event.target.value })} />
              </label>
              <label>Amount
                <input type="text" inputMode="decimal" value={draft.amount} onChange={(event) => change({ amount: event.target.value })} />
              </label>
            </>
          )}
          <label>Personal category
            <select value={draft.personalCategoryId} onChange={(event) => change({ personalCategoryId: event.target.value })}>
              <option value="">{fromExpense ? "As the expense" : "Choose a category"}</option>
              {personalCategories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
            </select>
          </label>
          <label>Business category
            <select value={draft.businessCategoryId} onChange={(event) => change({ businessCategoryId: event.target.value })}>
              <option value="">No category</option>
              {businessCategories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
            </select>
          </label>
          <label>Expected repayment date
            <input type="date" value={draft.expectedDate} onChange={(event) => change({ expectedDate: event.target.value })} />
          </label>
          {error && <p className="recurring-error plan-form-error" role="alert">{error}</p>}
          <div className="plan-actions">
            <button type="submit" className="button primary" disabled={busy}>Add</button>
            <button type="button" className="button secondary" onClick={() => { setAdding(false); setDraft(emptyDraft); setError(""); }}>Cancel</button>
          </div>
        </form>
      )}
      {!adding && error && <p className="recurring-error" role="alert">{error}</p>}
      {claims.length > 0 ? (
        <div className="table-scroll">
          <table className="rules-table plan-table" aria-label="Claims">
            <thead>
              <tr><th>Date</th><th>Description</th><th className="numeric">Amount</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {claims.map((claim) => (
                <tr key={claim.id}>
                  <td className="nowrap">{claim.date}</td>
                  <td className="truncate-cell" title={claim.description}>{claim.description}</td>
                  <td className="numeric nowrap">
                    {money(claim.amount)}{claim.share !== null && <span className="muted"> · {claim.share}%</span>}
                  </td>
                  <td className="nowrap">
                    <span className={`pay-tag${claim.paid ? "" : " tag-amber"}`}>{status(claim)}</span>
                  </td>
                  <td className="rules-actions">
                    {claim.paid ? (
                      <button className="button secondary" aria-label={`Reopen claim ${claim.description}`} disabled={busy} onClick={() => void patch(claim, { paid_transaction_id: null }, "Reopened the claim.")}>Reopen</button>
                    ) : (
                      <select
                        className="claim-paid-select"
                        aria-label={`Mark claim ${claim.description} paid`}
                        value=""
                        disabled={busy || (data?.candidates.length ?? 0) === 0}
                        onChange={(event) => { if (event.target.value) void patch(claim, { paid_transaction_id: Number(event.target.value) }, "Marked the claim paid."); }}
                      >
                        <option value="">{(data?.candidates.length ?? 0) === 0 ? "No repayments to match" : "Mark paid…"}</option>
                        {candidatesFor(claim).map((item) => (
                          <option key={item.id} value={item.id}>{item.date} · {item.merchant} · {money(item.amount)}</option>
                        ))}
                      </select>
                    )}
                    <button className="button secondary" aria-label={`Delete claim ${claim.description}`} disabled={busy} onClick={() => void remove(claim)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">No claims.</p>}
    </section>
  );
}
