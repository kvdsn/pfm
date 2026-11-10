import { useEffect, useRef, useState } from "react";
import { ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS, type AccountOverview, type BalanceCheck, type ImportRecord, type Statement } from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";
import { useArrowSelection } from "./hooks";
import { ImportFileInput, ImportPreviewPanel, type ImportFlow } from "./Imports";

/** A last transaction older than this many days suggests a new export is due. */
export const STALE_AFTER_DAYS = 35;
const DAY_MS = 86_400_000;
const COLUMNS = 7;

/** Whole days from an ISO date (YYYY-MM-DD) to `today`'s calendar day. */
export function daysSince(isoDate: string, today: Date): number {
  const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const [year, month, day] = isoDate.split("-").map(Number);
  return Math.round((todayUtc - Date.UTC(year, month - 1, day)) / DAY_MS);
}

const STATUS_LABELS: Record<Statement["status"], string> = {
  paid: "Paid",
  credit: "Credit carried over",
  nothing_due: "Nothing to pay",
  unpaid: "No payment found",
};

/** What happened to a statement's amount, with the payment's date and bank account when one was found. */
export function statementStatusTitle(statement: Statement): string {
  switch (statement.status) {
    case "paid":
      return statement.settlement
        ? `Paid on ${statement.settlement.booking_date} from ${statement.settlement.account}`
        : "Paid";
    case "credit": return "Refunds outweighed purchases; the credit lowers the next payment";
    case "nothing_due": return "Nothing was left to pay";
    default: return "No matching bank payment is imported yet";
  }
}

/** A statement's amount: what was paid or is due, or the credit it carries over. */
function statementAmount(statement: Statement): string {
  if (statement.status === "credit") return `+${money(statement.total)}`;
  return money(statement.due);
}

function UndoButton({ record, flow, onUndo }: { record: ImportRecord; flow: ImportFlow; onUndo: (record: ImportRecord) => void }) {
  return (
    <button
      className="button secondary undo-import"
      aria-label={`Undo import ${record.filename}`}
      title={`${record.filename}, imported ${new Date(record.imported_at).toLocaleString()}`}
      disabled={flow.busy}
      onClick={() => onUndo(record)}
    >Undo</button>
  );
}

/**
 * A card's statements, newest first, beside the imports that brought them: each Undo import spans the
 * statements of its file, and imports that brought no statement follow at the end.
 */
function CardStatements({ account, flow, reloads, onUndo }: {
  account: AccountOverview;
  flow: ImportFlow;
  reloads: number;
  onUndo: (record: ImportRecord) => void;
}) {
  const [statements, setStatements] = useState<Statement[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let current = true;
    setFailed(false);
    api<Statement[]>(`/api/accounts/${account.id}/statements`)
      .then((items) => { if (current) setStatements(items); })
      .catch(() => { if (current) { setStatements([]); setFailed(true); } });
    return () => { current = false; };
  }, [account.id, reloads]);
  const imports = account.imports ?? [];
  const importsById = new Map(imports.map((record) => [record.id, record]));
  const rows = statements ?? [];
  // How many rows each import's Undo cell spans: consecutive statements of the same import share one.
  const spans = rows.map((statement, index) => {
    if (index > 0 && rows[index - 1].import_id === statement.import_id) return 0;
    let span = 1;
    while (index + span < rows.length && rows[index + span].import_id === statement.import_id) span += 1;
    return span;
  });
  const withStatements = new Set(rows.map((statement) => statement.import_id));
  const otherImports = statements ? imports.filter((record) => !withStatements.has(record.id)) : [];
  return (
    <>
      <h4 className="category-details-subheading">Statements and imports</h4>
      {failed && <p className="muted small">The statements could not be loaded.</p>}
      {!statements && <p className="muted small">Loading…</p>}
      {statements && (
        <table className="card-statements-table">
          <thead>
            <tr><th>Debited</th><th className="numeric">Amount</th><th>Payment</th><th className="statement-import">Import</th></tr>
          </thead>
          <tbody>
            {rows.map((statement, index) => {
              const record = importsById.get(statement.import_id);
              return (
                <tr key={statement.id}>
                  <td>
                    <span className="nowrap">{statement.debit_date}</span>
                    <span className="muted small statement-detail">
                      {statement.purchase_count} {statement.purchase_count === 1 ? "purchase" : "purchases"}
                    </span>
                  </td>
                  <td className="numeric nowrap">{statementAmount(statement)}</td>
                  <td className={statement.status === "unpaid" ? "statement-unpaid" : statement.status === "paid" ? "" : "muted"} title={statementStatusTitle(statement)}>
                    {STATUS_LABELS[statement.status]}
                    {statement.settlement && <span className="muted small statement-detail">from {statement.settlement.account}</span>}
                  </td>
                  {spans[index] > 0 && (
                    <td rowSpan={spans[index]} className="statement-import">
                      {record && <UndoButton record={record} flow={flow} onUndo={onUndo} />}
                    </td>
                  )}
                </tr>
              );
            })}
            {otherImports.map((record) => (
              <tr key={`import-${record.id}`}>
                <td colSpan={3} className="muted small">
                  {new Date(record.imported_at).toLocaleDateString()} · no new statements ({record.duplicate_count} duplicates skipped)
                </td>
                <td className="statement-import"><UndoButton record={record} flow={flow} onUndo={onUndo} /></td>
              </tr>
            ))}
            {!rows.length && !otherImports.length && (
              <tr><td colSpan={4} className="muted small">No statements.</td></tr>
            )}
          </tbody>
        </table>
      )}
    </>
  );
}

/** The details of the selected account: its number and its imports, newest first, each with Undo import; a card lists its statements with them. */
function AccountDetails({ account, flow, reloads, onUndo }: {
  account: AccountOverview;
  flow: ImportFlow;
  reloads: number;
  onUndo: (record: ImportRecord) => void;
}) {
  const imports = account.imports ?? [];
  return (
    <aside className="panel impact-pane account-details" aria-label={`Details of ${account.label}`}>
      <div className="impact-heading">
        <h3>{account.label}</h3>
      </div>
      <p className="impact-summary">{account.masked}</p>
      {account.kind === "bank" && <BalanceWarnings check={account.balance_check} />}
      {account.kind === "card" ? <CardStatements account={account} flow={flow} reloads={reloads} onUndo={onUndo} /> : (
        <>
          <h4 className="category-details-subheading">Imports</h4>
          <div className="impact-matches">
            {imports.map((item) => (
              <div className="account-import" key={item.id}>
                <span className="account-import-file" title={item.filename}>{item.filename}</span>
                <span className="muted small">
                  {new Date(item.imported_at).toLocaleString()} · {item.transaction_count} transactions · {item.duplicate_count} duplicates skipped
                </span>
                <button className="button secondary" disabled={flow.busy} onClick={() => onUndo(item)}>Undo import</button>
              </div>
            ))}
            {!imports.length && <p className="muted small">No imports.</p>}
          </div>
        </>
      )}
    </aside>
  );
}

const MAX_SHOWN_GAPS = 3;

/** Quiet warnings from the balance check; nothing when there are no gaps and nothing unchecked. */
function BalanceWarnings({ check }: { check?: BalanceCheck | null }) {
  if (!check || (!check.gaps.length && !check.unchecked)) return null;
  const shown = check.gaps.slice(0, MAX_SHOWN_GAPS);
  const more = check.gaps.length - shown.length;
  return (
    <div className="balance-check">
      {shown.map((gap) => (
        <p className="plan-warning small" key={gap.before}>
          {gap.after
            ? `Transactions may be missing between ${gap.after} and ${gap.before}`
            : `Transactions may be missing before ${gap.before}`}
        </p>
      ))}
      {more > 0 && <p className="plan-warning small">and {more} more</p>}
      {check.unchecked > 0 && (
        <p className="muted small">
          {check.unchecked} {check.unchecked === 1 ? "transaction has" : "transactions have"} no balance to check
        </p>
      )}
    </div>
  );
}

/** The accounts and cards of every entity beside the selected one's imports, with the import previews on top. */
export function AccountsView({
  flow,
  onClose,
  today = new Date(),
  active = true,
}: {
  /** Whether the arrow keys move through the accounts; the page shares them with the backups below. */
  active?: boolean;
  flow: ImportFlow;
  onClose: () => void;
  /** The current day; tests pass a fixed one to check the staleness warning. */
  today?: Date;
}) {
  const [accounts, setAccounts] = useState<AccountOverview[] | null>(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reloads, setReloads] = useState(0);
  const tableBody = useRef<HTMLTableSectionElement>(null);
  // Committing or cancelling previews changes the imports, so the list reloads when the previews do.
  const previewCount = flow.previews.length;

  useEffect(() => {
    let current = true;
    api<AccountOverview[]>("/api/accounts/overview")
      .then((items) => {
        if (!current) return;
        setAccounts(items);
        setError("");
        // Start on the account imported most recently; keep the choice when reloading.
        setSelectedId((chosen) => {
          if (chosen !== null && items.some((item) => item.id === chosen)) return chosen;
          const latest = items.reduce<AccountOverview | null>(
            (best, item) => (item.last_import_at && (!best?.last_import_at || item.last_import_at > best.last_import_at) ? item : best),
            null,
          );
          return latest?.id ?? null;
        });
      })
      .catch((cause) => { if (current) setError(errorMessage(cause, "Could not load the accounts")); });
    return () => { current = false; };
  }, [previewCount, reloads]);

  const groups = ACCOUNT_TYPES
    .map((kind) => ({ kind, items: (accounts ?? []).filter((account) => account.account_type === kind) }))
    .filter((group) => group.items.length);
  useArrowSelection(groups.flatMap((group) => group.items.map((item) => item.id)), selectedId, (id) => {
    setSelectedId(id);
    tableBody.current?.querySelector(`[data-account-id="${id}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, active);
  const selected = accounts?.find((account) => account.id === selectedId);

  const undo = async (record: ImportRecord) => {
    await flow.undoImport(record);
    setReloads((count) => count + 1);
  };

  return (
    <div className="rules-view">
      <section className="panel rules-pane" aria-label="Accounts">
        <div className="section-heading">
          <div><h3>Accounts and cards <span className="count">{accounts?.length ?? 0}</span></h3></div>
          <div className="transaction-filters">
            <ImportFileInput className="button primary choose-file" disabled={flow.busy} onFiles={(files) => void flow.previewFiles(files)}>
              <span>{flow.busy ? "Working…" : "Import files"}</span>
            </ImportFileInput>
            <button className="icon-button" aria-label="Close accounts" onClick={onClose}>×</button>
          </div>
        </div>
        <p className="muted small">
          Every imported account and card, with the period its transactions cover. Import a new export when the last transaction is getting old.
          Import bank account CSVs from KBC, Belfius or Revolut, KBC card CSVs, and Belfius card statement PDFs; select several at once.
        </p>
        {error && <p className="warning">{error}</p>}
        <ImportPreviewPanel flow={flow} />
        <div className="table-scroll">
          <table className="rules-table accounts-table">
            <thead>
              <tr>
                <th>Bank and kind</th><th>Number</th><th className="numeric">Transactions</th>
                <th>First</th><th>Last</th><th className="numeric">Latest balance</th><th>Last import</th>
              </tr>
            </thead>
            <tbody ref={tableBody}>
              {groups.map((group) => [
                <tr key={group.kind} className="account-group-row">
                  <th colSpan={COLUMNS} scope="colgroup">{ACCOUNT_TYPE_LABELS[group.kind]}</th>
                </tr>,
                ...group.items.map((account) => {
                  const age = account.last_date ? daysSince(account.last_date, today) : 0;
                  return (
                    <tr
                      key={account.id}
                      data-account-id={account.id}
                      className={account.id === selectedId ? "selected-rule" : ""}
                      onClick={() => setSelectedId(account.id)}
                    >
                      <td>{account.bank} · {account.kind === "card" ? "card" : "account"}</td>
                      <td className="nowrap">{account.masked}</td>
                      <td className="numeric">{account.transaction_count}</td>
                      <td className="nowrap">{account.first_date ?? "—"}</td>
                      <td>
                        <span className="nowrap">{account.last_date ?? "—"}</span>
                        {age > STALE_AFTER_DAYS && <span className="muted small stale-age"> {age} days ago</span>}
                      </td>
                      <td className="numeric">
                        {money(account.latest_balance)}
                        {account.balance_check?.gaps.length ? <span className="plan-warning" title="Transactions may be missing"> !</span> : null}
                      </td>
                      <td>{account.last_import_at ? new Date(account.last_import_at).toLocaleString() : "—"}</td>
                    </tr>
                  );
                }),
              ])}
              {accounts && !accounts.length && (
                <tr><td colSpan={COLUMNS} className="muted">No accounts yet.</td></tr>
              )}
              {!accounts && !error && (
                <tr><td colSpan={COLUMNS} className="muted">Loading…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      {selected && <AccountDetails account={selected} flow={flow} reloads={reloads + previewCount} onUndo={(record) => void undo(record)} />}
    </div>
  );
}
