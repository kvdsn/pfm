import { useState } from "react";
import type { DirectorLoan, DirectorLoanYear } from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";

type Draft = { date: string; amount: string; note: string };

/** The form's starting values: the unpaid interest through the year's end, else the year's own interest. */
function startingDraft(year: DirectorLoanYear): Draft {
  const unpaid = Number(year.interest_unpaid);
  return { date: `${year.year}-12-31`, amount: unpaid > 0 ? year.interest_unpaid : year.interest, note: "" };
}

/** Personal's debt to Business: the Entity transfers since the loan start, and the simple interest on them. */
export function DirectorLoanSection({
  loan, onLoan, rules, onChanged, onNotice,
}: {
  /** Null when the loan is not available (an older server). */
  loan: DirectorLoan | null;
  onLoan: (loan: DirectorLoan) => void;
  /** The Loan rules form, built by PlanView. */
  rules: React.ReactNode;
  /** Called after interest was planned, so the forecast reloads. */
  onChanged: () => void;
  onNotice: (message: string) => void;
}) {
  const [planningYear, setPlanningYear] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>({ date: "", amount: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showMovements, setShowMovements] = useState(false);

  const open = (year: DirectorLoanYear) => {
    setError("");
    setDraft(startingDraft(year));
    setPlanningYear(year.year);
  };
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const body: Record<string, string> = { date: draft.date, amount: draft.amount.trim() };
      if (draft.note.trim() !== "") body.note = draft.note.trim();
      const next = await api<DirectorLoan>("/api/forecast/director-loan/interest", { method: "POST", json: body });
      onLoan(next);
      setPlanningYear(null);
      onChanged();
      onNotice(`Planned the director loan interest of ${money(draft.amount.trim())} on ${draft.date}.`);
    } catch (cause) {
      setError(errorMessage(cause, "Could not plan the interest"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel plan-panel" aria-label="Director loan">
      <h4 className="plan-heading">Director loan</h4>
      {loan === null ? <p className="muted small">The loan is not available.</p> : (
        <>
          <p className="plan-line">
            <span className="muted">Loan </span><strong>{money(loan.balance)}</strong>
            <span className="muted"> · interest so far </span><strong>{money(loan.interest_to_date)}</strong>
            <span className="muted"> · paid </span><strong>{money(loan.interest_paid)}</strong>
          </p>
          {loan.notes.map((note) => <p className="plan-line plan-warning" key={note}>{note}</p>)}
          <div className="table-scroll">
            <table className="rules-table plan-table loan-table" aria-label="Loan per year">
              <thead>
                <tr>
                  <th>Year</th>
                  <th className="numeric">Transfers</th>
                  <th className="numeric">Year end</th>
                  <th className="numeric">Interest</th>
                  <th className="numeric">Paid / planned</th>
                  <th className="numeric">Unpaid</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {loan.years.map((year) => {
                  const projected = Number(year.transfers_projected);
                  const paid = (Number(year.interest_paid) + Number(year.interest_planned)).toFixed(2);
                  return [
                    <tr key={year.year}>
                      <td className="nowrap">{year.year}</td>
                      <td className="numeric">
                        <span className="nowrap">{money(year.transfers)}</span>
                        {projected !== 0 && <span className="projected nowrap">+ {money(year.transfers_projected)} projected</span>}
                      </td>
                      <td className="numeric nowrap">{money(year.closing)}</td>
                      <td className="numeric nowrap">{money(year.interest)}</td>
                      <td className="numeric nowrap">{money(paid)}</td>
                      <td className="numeric nowrap">{money(year.interest_unpaid)}</td>
                      <td className="rules-actions">
                        <button className="button secondary" aria-label={`Plan interest ${year.year}`} disabled={busy} onClick={() => open(year)}>Plan interest</button>
                      </td>
                    </tr>,
                    planningYear === year.year && (
                      <tr key={`${year.year}-form`} className="loan-form-row">
                        <td colSpan={7}>
                          <form className="recurring-form loan-form" aria-label={`Plan interest for ${year.year}`} onSubmit={(event) => { event.preventDefault(); void save(); }}>
                            <label>Date
                              <input type="date" value={draft.date} onChange={(event) => setDraft({ ...draft, date: event.target.value })} />
                            </label>
                            <label>Amount
                              <input type="text" inputMode="decimal" value={draft.amount} onChange={(event) => setDraft({ ...draft, amount: event.target.value })} />
                            </label>
                            <label>Note (optional)
                              <input type="text" value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} />
                            </label>
                            <div className="plan-actions">
                              <button type="submit" className="button primary" disabled={busy}>Save</button>
                              <button type="button" className="button secondary" onClick={() => setPlanningYear(null)}>Cancel</button>
                            </div>
                            {error && <p className="recurring-error" role="alert">{error}</p>}
                          </form>
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
          {loan.movements.length > 0 && (
            <>
              <button className="recurring-toggle" aria-expanded={showMovements} onClick={() => setShowMovements((shown) => !shown)}>
                {showMovements ? "Hide" : "Show"} the movements
              </button>
              {showMovements && (
                <div className="table-scroll">
                  <table className="rules-table plan-table loan-table" aria-label="Loan movements">
                    <thead><tr><th>Date</th><th className="numeric">Amount</th><th className="numeric">Balance</th></tr></thead>
                    <tbody>
                      {loan.movements.map((move) => (
                        <tr key={`${move.date}\u0000${move.amount}\u0000${move.projected}`} className={move.projected ? "projected" : undefined}>
                          <td className="nowrap">{move.date}{move.projected ? " (projected)" : ""}</td>
                          <td className="numeric nowrap">{money(move.amount)}</td>
                          <td className="numeric nowrap">{money(move.balance)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
      <p className="muted small">Entity transfers from Business since the start date count as the loan. Interest is simple, per day, on the balance. Reserve payouts that pay off the loan are entries in the company's books, not bank transfers, so they don't appear here.</p>
      <h5 className="plan-subheading">Loan rules</h5>
      {rules}
    </section>
  );
}
