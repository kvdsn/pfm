import type { ForecastVsActual, VsHint, VsMiss } from "./types";
import { money } from "./format";

const HINT_LABELS: Record<VsHint, string> = {
  amount_changed: "Amount changed",
  one_off: "One-off",
  make_recurring: "Make recurring?",
  baseline_low: "Baseline too low",
  baseline_high: "Baseline too high",
};

const monthName = (key: string) =>
  new Date(`${key}-01T00:00:00Z`).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });

/** A signed amount with cents: "+ € 48.00", "- € 12.50". */
const signedAmount = (value: string) => {
  const amount = Number(value);
  return amount === 0 ? money(0) : `${amount < 0 ? "−" : "+"} ${money(Math.abs(amount))}`;
};

/** Whether a difference is bad news: more spent, or less income. */
const isBad = (difference: number, flow: "income" | "expense") => (flow === "expense" ? difference > 0 : difference < 0);

function Hint({ miss, onOpenRecurring }: { miss: VsMiss; onOpenRecurring: (id: number | null) => void }) {
  if (!miss.hint) return null;
  const text = HINT_LABELS[miss.hint];
  if (miss.hint === "one_off") return <span className="muted">{text}</span>;
  const open = miss.hint === "amount_changed" || miss.hint === "make_recurring"
    ? () => onOpenRecurring(miss.hint === "amount_changed" ? miss.recurring_id : null)
    : () => document.querySelector('[aria-label="Baselines"]')?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  const title = miss.hint === "amount_changed" ? "Open this payment in Recurring payments"
    : miss.hint === "make_recurring" ? "Open Recurring payments" : "Go to the Baselines settings";
  return <button className="vs-hint-link" title={title} onClick={open}>{text}</button>;
}

/** The Forecast page's "Forecast vs reality" panel: how far off the forecast was, and where. */
export function ForecastVsReality({
  vsActual,
  onOpenRecurring,
}: {
  vsActual: ForecastVsActual;
  /** Opens a confirmed recurring item, or only the Recurring section when the id is null. */
  onOpenRecurring: (id: number | null) => void;
}) {
  const { summary, pace, misses } = vsActual;
  const ahead = pace?.categories.filter((category) => category.ahead && category.flow === "expense") ?? [];
  const paceDifference = pace ? Number(pace.expense.difference) : null;
  const typical = summary.typical_balance_miss;
  return (
    <section className="panel plan-panel" aria-label="Forecast vs reality">
      <h4 className="plan-heading">Forecast vs reality</h4>
      <p className="muted small">What the forecast expected for a month when the app first showed it, next to what happened.</p>
      {vsActual.months.length === 0 ? (
        <p className="muted small">
          The first comparison appears after the first full month with a snapshot: the app saves one the first time it computes the forecast in a month.
        </p>
      ) : (
        <>
          <div className="vs-tiles">
            <div className="vs-tile" aria-label="Typical month-end miss">
              <div className="vs-tile-label">Typical month-end miss</div>
              <div className="vs-tile-value">{typical === null ? "—" : money(typical)}</div>
              <div className="vs-tile-note">Closing balance, median of complete months</div>
            </div>
            <div className="vs-tile" aria-label="This month's pace">
              <div className="vs-tile-label">This month's pace</div>
              <div className={`vs-tile-value${paceDifference !== null && Math.round(paceDifference) !== 0 ? (paceDifference > 0 ? " bad" : " good") : ""}`}>
                {paceDifference === null ? "—" : signedAmount(pace!.expense.difference)}
              </div>
              <div className="vs-tile-note">
                {pace ? `Expenses against the forecast so far, through ${pace.through}` : "No snapshot for this month yet"}
              </div>
            </div>
            <div className="vs-tile" aria-label="Months tracked">
              <div className="vs-tile-label">Months tracked</div>
              <div className="vs-tile-value">{summary.months_tracked}</div>
              <div className="vs-tile-note">Complete months compared</div>
            </div>
          </div>
          {ahead.length > 0 && (
            <>
              <h5 className="plan-subheading">Running ahead this month</h5>
              <ul className="vs-pace-list" aria-label="Categories running ahead">
                {ahead.map((category) => (
                  <li key={`${category.category_id ?? category.category}`} title={`Expected ${money(category.expected)} so far, actual ${money(category.actual)}`}>
                    {category.category} {signedAmount(category.difference)}
                  </li>
                ))}
              </ul>
            </>
          )}
          <h5 className="plan-subheading">Biggest misses</h5>
          {misses.length > 0 ? (
            <div className="table-scroll">
              <table className="rules-table plan-table" aria-label="Biggest misses">
                <thead>
                  <tr>
                    <th>Month</th><th>Category</th><th className="numeric">Forecast</th><th className="numeric">Actual</th>
                    <th className="numeric">Difference</th><th>Hint</th>
                  </tr>
                </thead>
                <tbody>
                  {misses.map((miss) => (
                    <tr key={`${miss.month}\u0000${miss.category_id ?? miss.category}\u0000${miss.flow}`}>
                      <td className="nowrap">{monthName(miss.month)}</td>
                      <td className="truncate-cell" title={miss.category}>{miss.category}</td>
                      <td className="numeric">{money(miss.forecast)}</td>
                      <td className="numeric">{money(miss.actual)}</td>
                      <td className={`numeric vs-diff ${isBad(Number(miss.difference), miss.flow) ? "bad" : "good"}`}>{signedAmount(miss.difference)}</td>
                      <td className="nowrap"><Hint miss={miss} onOpenRecurring={onOpenRecurring} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="muted small">No misses in complete months yet.</p>}
        </>
      )}
    </section>
  );
}
