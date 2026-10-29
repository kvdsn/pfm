import { useState } from "react";
import type { CorporateTaxYear, ForecastSettings, PlannedItem } from "./types";
import { api, errorMessage } from "./api";
import { Dialog } from "./Dialog";
import { money } from "./format";

const ADVICE_NOTE = "Corporate tax prepayment (advised)";

/** The planned corporate-tax items that "Use advice" would replace: the year's, dated today or later. */
function replacedItems(year: CorporateTaxYear, planned: PlannedItem[], taxCategoryId: number | null, today: string) {
  return planned.filter((item) =>
    taxCategoryId !== null && item.category_id === taxCategoryId && item.date >= today && item.date.startsWith(`${year.year}-`));
}

function YearEstimate({
  year, settings, busy, onAdvice,
}: {
  year: CorporateTaxYear;
  settings: ForecastSettings;
  busy: boolean;
  onAdvice: () => void;
}) {
  const [showBreakdown, setShowBreakdown] = useState(false);
  const surcharge = Number(year.surcharge_as_planned);
  const rate = year.eligible
    ? `Reduced rate: salary ${money(year.salary)} ≥ ${money(settings.tax_reduced_threshold)}`
    : `Standard rate: salary ${money(year.salary)} < ${money(settings.tax_reduced_threshold)}`;
  const advised = year.deadlines.some((row) => !row.past && Number(row.advised) > 0);
  return (
    <div className="tax-year" role="group" aria-label={`Corporate tax ${year.year}`}>
      <h5 className="plan-subheading">{year.year}</h5>
      <p className="plan-line"><span className="muted">Estimated profit: </span><strong>{money(year.profit)}</strong></p>
      <p className="plan-line"><span className="muted">{rate}</span></p>
      <p className="plan-line"><span className="muted">Estimated tax: </span><strong>{money(year.tax)}</strong></p>
      <button className="recurring-toggle" aria-expanded={showBreakdown} onClick={() => setShowBreakdown((shown) => !shown)}>
        {showBreakdown ? "Hide" : "Show"} the profit breakdown
      </button>
      {showBreakdown && (
        <div className="table-scroll">
          <table className="rules-table plan-table" aria-label={`Profit breakdown ${year.year}`}>
            <thead><tr><th>Category</th><th className="numeric">Profit</th></tr></thead>
            <tbody>
              {year.breakdown.map((row) => (
                <tr key={row.category}>
                  <td className="truncate-cell">{row.category}</td>
                  <td className={`numeric ${Number(row.amount) < 0 ? "negative" : "positive"}`}>{money(row.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="table-scroll">
        <table className="rules-table plan-table" aria-label={`Prepayments ${year.year}`}>
          <thead>
            <tr><th>Deadline</th><th className="numeric">Prepaid</th><th className="numeric">Planned</th><th className="numeric">Advised</th></tr>
          </thead>
          <tbody>
            {year.deadlines.map((row) => (
              <tr key={row.date} className={row.past ? "tax-past" : undefined}>
                <td className="nowrap">{row.date}</td>
                <td className="numeric nowrap">{money(row.prepaid)}</td>
                <td className="numeric nowrap">{money(row.planned)}</td>
                <td className="numeric nowrap">{row.past ? "—" : money(row.advised)}</td>
              </tr>
            ))}
            <tr>
              <td><strong>Total</strong></td>
              <td className="numeric nowrap"><strong>{money(year.prepaid_total)}</strong></td>
              <td className="numeric nowrap"><strong>{money(year.planned_total)}</strong></td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <p className={`plan-line${surcharge > 0 ? " plan-warning" : ""}`}>Surcharge as planned: {money(year.surcharge_as_planned)}</p>
      {year.closed ? (
        <p className="muted small">The prepayments for {year.year} are closed.</p>
      ) : (
        <div className="plan-actions">
          <button className="button secondary" disabled={busy || !advised} onClick={onAdvice}>Use advice for {year.year}</button>
        </div>
      )}
      {year.assumptions.length > 0 && (
        <ul className="plan-list muted">{year.assumptions.map((text) => <li key={text}>{text}</li>)}</ul>
      )}
    </div>
  );
}

/** Business's corporate tax: the estimate per year, what is prepaid and planned, and the advised prepayments. */
export function CorporateTaxSection({
  tax, settings, planned, today, rules, onChanged, onNotice,
}: {
  tax: CorporateTaxYear[] | null;
  settings: ForecastSettings;
  planned: PlannedItem[];
  /** ISO date; planned items from this day on are replaced by the advice. */
  today: string;
  /** The Tax rules form, built by PlanView. */
  rules: React.ReactNode;
  /** Called after the advice was applied, so the view and the forecast reload. */
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState<CorporateTaxYear | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const apply = async (year: CorporateTaxYear) => {
    setBusy(true);
    setError("");
    try {
      await api(`/api/forecast/corporate-tax/${year.year}/apply`, { method: "POST" });
      setConfirming(null);
      await onChanged();
      onNotice(`Planned the advised corporate tax prepayments for ${year.year}.`);
    } catch (cause) {
      setError(errorMessage(cause, "Could not plan the prepayments"));
    } finally {
      setBusy(false);
    }
  };
  const replaced = confirming ? replacedItems(confirming, planned, settings.roles.corporate_tax, today) : [];
  const created = confirming?.deadlines.filter((row) => !row.past && Number(row.advised) > 0) ?? [];
  return (
    <section className="panel plan-panel" aria-label="Corporate tax">
      <h4 className="plan-heading">Corporate tax</h4>
      <p className="muted small">An estimate from the tax rules below, to confirm with your accountant. Prepayments avoid the surcharge.</p>
      {tax === null ? <p className="muted small">The estimate is not available.</p> : (
        <div className="tax-years">
          {tax.map((year) => (
            <YearEstimate key={year.year} year={year} settings={settings} busy={busy} onAdvice={() => { setError(""); setConfirming(year); }} />
          ))}
        </div>
      )}
      {!confirming && error && <p className="recurring-error" role="alert">{error}</p>}
      <h5 className="plan-subheading">Tax rules</h5>
      {rules}
      {confirming && (
        <Dialog
          title={`Use the advice for ${confirming.year}`}
          closeLabel="Close the advice"
          className="tax-dialog"
          backdropClassName="tax-backdrop"
          onClose={() => setConfirming(null)}
        >
          <p className="plan-line">Planned corporate tax items from today on in {confirming.year} will be replaced:</p>
          {replaced.length > 0 ? (
            <ul className="plan-list">
              {replaced.map((item) => <li key={item.id}>{item.date} · {money(item.amount)}{item.note ? ` · ${item.note}` : ""}</li>)}
            </ul>
          ) : <p className="muted small">None.</p>}
          <p className="plan-line">With one item per remaining deadline:</p>
          <ul className="plan-list">
            {created.map((row) => <li key={row.date}>{row.date} · {money(-Number(row.advised))} · {ADVICE_NOTE}</li>)}
          </ul>
          {error && <p className="recurring-error" role="alert">{error}</p>}
          <div className="plan-actions">
            <button className="button primary" disabled={busy} onClick={() => void apply(confirming)}>Replace planned items</button>
            <button className="button secondary" onClick={() => setConfirming(null)}>Cancel</button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
