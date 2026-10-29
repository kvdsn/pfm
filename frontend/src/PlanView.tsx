import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ACCOUNT_TYPES,
  ACCOUNT_TYPE_LABELS,
  type AccountType,
  type Category,
  type ConsultingCheck,
  type ConsultingMonth,
  type CorporateTax,
  type CorporateTaxYear,
  type DirectorLoan,
  type DayOff,
  type DayRate,
  type Forecast,
  type ForecastRole,
  type ForecastSettings,
  type PlannedItem,
  type SalarySchedule,
} from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";
import { tipMoney } from "./forecast";
import { useSignal } from "./hooks";
import { CorporateTaxSection } from "./CorporateTax";
import { ReimbursementsSection } from "./Reimbursements";
import { DirectorLoanSection } from "./DirectorLoan";

const UNCATEGORIZED = "Uncategorized";
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
/** The entity each model role books in, and how the role reads in the settings. */
const ROLES: Record<ForecastRole, { entity: AccountType; label: string }> = {
  consulting_income: { entity: "business", label: "Consulting income" },
  vat: { entity: "business", label: "VAT" },
  corporate_tax: { entity: "business", label: "Corporate tax" },
  salary_business: { entity: "business", label: "Salary paid out (director remuneration)" },
  transfer_business: { entity: "business", label: "Transfer to Personal" },
  salary_personal: { entity: "personal", label: "Salary received" },
  transfer_personal: { entity: "personal", label: "Transfer from Business" },
  loan_interest_business: { entity: "business", label: "Director loan interest received" },
  loan_interest_personal: { entity: "personal", label: "Director loan interest paid" },
};
const REPEAT_CHOICES: [number, string][] = [[0, "No"], [1, "Every week"], [2, "Every 2 weeks"], [3, "Every 3 weeks"], [4, "Every 4 weeks"]];
const repeatLabel = (item: DayOff) => {
  if (!item.repeat_weeks) return "No";
  const every = item.repeat_weeks === 1 ? "Every week" : `Every ${item.repeat_weeks} weeks`;
  return item.repeat_until ? `${every} until ${item.repeat_until}` : every;
};
const SALARY_SHARE = /Personal receives ([\d.]+)% of the salary/;

const signClass = (amount: string) => (Number(amount) < 0 ? "negative" : "positive");
const monthLabel = (key: string) =>
  new Date(`${key}-01T00:00:00Z`).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });

// --- Settings sections ----------------------------------------------------------------

type Field =
  | { kind: "text" | "number"; key: keyof ForecastSettings; label: string; help?: string }
  | { kind: "checkbox"; key: keyof ForecastSettings; label: string }
  /** This entity's closed categories: left out of the baseline. */
  | { kind: "closed"; label: string }
  /** The Business categories left out of the tax profit. */
  | { kind: "excluded"; label: string }
  /** The categories the models book in, for the given roles. */
  | { kind: "roles"; roles: ForecastRole[] };

type Draft = Record<string, unknown>;
const fieldId = (field: Field) => (field.kind === "closed" || field.kind === "excluded" || field.kind === "roles" ? field.kind : String(field.key));

function initialDraft(fields: Field[], settings: ForecastSettings, accountType: AccountType): Draft {
  const draft: Draft = {};
  for (const field of fields) {
    if (field.kind === "closed") draft.closed = [...settings.closed_categories[accountType]].sort((a, b) => a - b);
    else if (field.kind === "excluded") draft.excluded = [...settings.tax_profit_excluded].sort((a, b) => a - b);
    else if (field.kind === "roles") draft.roles = Object.fromEntries(field.roles.map((role) => [role, settings.roles[role]]));
    else if (field.kind === "checkbox") draft[String(field.key)] = Boolean(settings[field.key]);
    else draft[String(field.key)] = String(settings[field.key]);
  }
  return draft;
}

/** The settings that differ from the saved ones, as the body of PUT /api/forecast/settings. */
function changesOf(fields: Field[], draft: Draft, initial: Draft, accountType: AccountType): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const field of fields) {
    const id = fieldId(field);
    if (JSON.stringify(draft[id]) === JSON.stringify(initial[id])) continue;
    if (field.kind === "closed") changes.closed_categories = { [accountType]: draft.closed };
    else if (field.kind === "excluded") changes.tax_profit_excluded = draft.excluded;
    else if (field.kind === "roles") {
      const before = initial.roles as Record<string, number | null>;
      const after = draft.roles as Record<string, number | null>;
      changes.roles = Object.fromEntries(Object.keys(after).filter((role) => after[role] !== before[role]).map((role) => [role, after[role]]));
    } else if (field.kind === "number") {
      const text = String(draft[id]).trim();
      // A number the server can read is sent as one; anything else goes as typed, for the server to explain.
      changes[String(field.key)] = text !== "" && !Number.isNaN(Number(text)) ? Number(text) : text;
    } else if (field.kind === "text") changes[String(field.key)] = String(draft[id]).trim();
    else changes[String(field.key)] = draft[id];
  }
  return changes;
}

function SettingsForm({
  fields, initial, categories, accountType, onSave, children,
}: {
  fields: Field[];
  initial: Draft;
  categories: Category[];
  accountType: AccountType;
  onSave: (changes: Record<string, unknown>) => Promise<void>;
  children?: React.ReactNode;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const changes = changesOf(fields, draft, initial, accountType);
  const set = (id: string, value: unknown) => setDraft((current) => ({ ...current, [id]: value }));
  const choices = categories.filter((category) => category.name !== UNCATEGORIZED);
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await onSave(changes);
    } catch (cause) {
      setError(errorMessage(cause, "Could not save the settings"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="recurring-form plan-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      {fields.map((field) => {
        if (field.kind === "checkbox") {
          return (
            <label className="plan-check" key={String(field.key)}>
              <input type="checkbox" checked={Boolean(draft[String(field.key)])} onChange={(event) => set(String(field.key), event.target.checked)} />
              {field.label}
            </label>
          );
        }
        if (field.kind === "closed" || field.kind === "excluded") {
          const id = field.kind === "closed" ? "closed" : "excluded";
          const closed = draft[id] as number[];
          return (
            <fieldset key={id}>
              <legend>{field.label}</legend>
              <div className="plan-checks">
                {choices.map((category) => (
                  <label className="plan-check" key={category.id}>
                    <input
                      type="checkbox"
                      checked={closed.includes(category.id)}
                      onChange={(event) => set(id, event.target.checked
                        ? [...closed, category.id].sort((a, b) => a - b)
                        : closed.filter((id) => id !== category.id))}
                    />
                    {category.name}
                  </label>
                ))}
              </div>
            </fieldset>
          );
        }
        if (field.kind === "roles") {
          const roles = draft.roles as Record<string, number | null>;
          return (
            <div className="plan-roles" key="roles">
              {field.roles.map((role) => (
                <label key={role}>{ROLES[role].label}
                  <select
                    value={roles[role] ?? ""}
                    onChange={(event) => set("roles", { ...roles, [role]: event.target.value === "" ? null : Number(event.target.value) })}
                  >
                    <option value="">None</option>
                    {choices.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
                  </select>
                </label>
              ))}
            </div>
          );
        }
        return (
          <label key={String(field.key)}>{field.label}
            <input
              type="text"
              inputMode={field.kind === "number" ? "decimal" : undefined}
              value={String(draft[String(field.key)])}
              onChange={(event) => set(String(field.key), event.target.value)}
            />
            {field.help && <span className="muted">{field.help}</span>}
          </label>
        );
      })}
      {children}
      {error && <p className="recurring-error" role="alert">{error}</p>}
      <div className="plan-actions">
        <button type="submit" className="button primary" disabled={busy || Object.keys(changes).length === 0}>Save</button>
      </div>
    </form>
  );
}

/** A section of settings with its own Save button; it starts over from the saved values when they change. */
function SettingsSection({
  fields, settings, categories, accountType, onSave, children,
}: {
  fields: Field[];
  settings: ForecastSettings;
  categories: Category[];
  accountType: AccountType;
  onSave: (changes: Record<string, unknown>) => Promise<void>;
  children?: React.ReactNode;
}) {
  const initial = useMemo(() => initialDraft(fields, settings, accountType), [fields, settings, accountType]);
  return (
    <SettingsForm
      key={JSON.stringify(initial)}
      fields={fields}
      initial={initial}
      categories={categories}
      accountType={accountType}
      onSave={onSave}
    >
      {children}
    </SettingsForm>
  );
}

// --- Planned items --------------------------------------------------------------------

type PlannedDraft = { date: string; amount: string; categoryId: string; note: string; linkId: string };
const emptyPlanned: PlannedDraft = { date: "", amount: "", categoryId: "", note: "", linkId: "" };
const plannedDraftOf = (item: PlannedItem): PlannedDraft => ({
  date: item.date, amount: item.amount, categoryId: item.category_id === null ? "" : String(item.category_id),
  note: item.note, linkId: item.linked ? String(item.linked.id) : "",
});

function PlannedForm({
  label, initial, categories, linkable, busy, error, onSave, onCancel,
}: {
  label: string;
  initial: PlannedDraft;
  categories: Category[];
  linkable: PlannedItem[];
  busy: boolean;
  error: string;
  onSave: (draft: PlannedDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const change = (patch: Partial<PlannedDraft>) => setDraft((current) => ({ ...current, ...patch }));
  return (
    <form className="recurring-form plan-form plan-planned-form" aria-label={label} onSubmit={(event) => { event.preventDefault(); onSave(draft); }}>
      <label>Date
        <input type="date" value={draft.date} onChange={(event) => change({ date: event.target.value })} />
      </label>
      <label>Amount
        <input type="text" inputMode="decimal" placeholder="Negative for money out" value={draft.amount} onChange={(event) => change({ amount: event.target.value })} />
      </label>
      <label>Category
        <select value={draft.categoryId} onChange={(event) => change({ categoryId: event.target.value })}>
          <option value="">No category</option>
          {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select>
      </label>
      <label>Note
        <input type="text" value={draft.note} onChange={(event) => change({ note: event.target.value })} />
      </label>
      <label>Linked to
        <select value={draft.linkId} onChange={(event) => change({ linkId: event.target.value })}>
          <option value="">Not linked</option>
          {linkable.map((item) => (
            <option key={item.id} value={item.id}>
              {[ACCOUNT_TYPE_LABELS[item.account_type], item.date, money(item.amount), item.note].filter(Boolean).join(" · ")}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="recurring-error plan-form-error" role="alert">{error}</p>}
      <div className="plan-actions">
        <button type="submit" className="button primary" disabled={busy}>Save</button>
        <button type="button" className="button secondary" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// --- Salary, day rates and days off ---------------------------------------------------

function SalaryRow({
  schedule, busy, onSave, onDelete,
}: {
  /** Null for the row that adds a year. */
  schedule: SalarySchedule | null;
  busy: boolean;
  onSave: (year: string, amount: string, lastMonth: number, minimum: string) => void;
  onDelete?: () => void;
}) {
  const [year, setYear] = useState(schedule?.year ?? "");
  const [amount, setAmount] = useState(schedule?.monthly_amount ?? "");
  const [last, setLast] = useState(String(schedule?.last_month ?? 12));
  const savedMinimum = schedule && Number(schedule.minimum_amount) ? schedule.minimum_amount : "";
  const [minimum, setMinimum] = useState(savedMinimum);
  const changed = !schedule || amount.trim() !== schedule.monthly_amount || Number(last) !== schedule.last_month
    || minimum.trim() !== savedMinimum;
  return (
    <tr>
      <td>{schedule ? schedule.year : <input type="text" inputMode="numeric" aria-label="New salary year" placeholder="YYYY" value={year} onChange={(event) => setYear(event.target.value)} />}</td>
      <td><input type="text" inputMode="decimal" aria-label={schedule ? `Monthly salary ${schedule.year}` : "New monthly salary"} value={amount} onChange={(event) => setAmount(event.target.value)} /></td>
      <td>
        <select aria-label={schedule ? `Last month ${schedule.year}` : "New last month"} value={last} onChange={(event) => setLast(event.target.value)}>
          {MONTH_NAMES.map((month, index) => <option key={month} value={index + 1}>{month}</option>)}
        </select>
      </td>
      <td>
        <input
          type="text"
          inputMode="decimal"
          aria-label={schedule ? `Minimum after ${schedule.year}'s last month` : "New minimum after the last month"}
          placeholder="None"
          value={minimum}
          onChange={(event) => setMinimum(event.target.value)}
        />
      </td>
      <td className="rules-actions">
        <button
          className="button secondary"
          aria-label={schedule ? `Save salary ${schedule.year}` : "Add salary year"}
          disabled={busy || !changed}
          onClick={() => onSave(schedule ? schedule.year : year.trim(), amount.trim(), Number(last), minimum.trim())}
        >{schedule ? "Save" : "Add"}</button>
        {onDelete && <button className="button secondary" aria-label={`Delete salary ${schedule?.year}`} disabled={busy} onClick={onDelete}>Delete</button>}
      </td>
    </tr>
  );
}

// --- Consulting payments ----------------------------------------------------------------

const PAYMENT_TAGS: Record<ConsultingMonth["status"], { label: string; tone: string }> = {
  paid: { label: "Paid", tone: "" }, fewer_days: { label: "Fewer days", tone: "" }, differs: { label: "Differs", tone: "tag-amber" },
  late: { label: "Late", tone: "tag-red" }, due: { label: "Due", tone: "" },
};
const VAT_TAGS = {
  paid: { label: "VAT paid", tone: "" }, differs: { label: "VAT differs", tone: "tag-amber" },
  missing: { label: "VAT missing", tone: "tag-red" }, due: { label: "VAT due", tone: "" },
};
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

function paymentTip(row: ConsultingMonth): string {
  if (row.status === "due") return `Expected ${money(row.expected)} by ${row.due}`;
  if (row.status === "late") return `Nothing received; ${money(row.expected)} was due ${row.due} (or not imported yet)`;
  const days = `${row.days_paid} days paid, ${row.days_expected} expected`;
  if (row.status === "paid") return days;
  const gap = row.days_expected - row.days_paid;
  if (row.status === "fewer_days") return `${plural(gap, "day")} fewer than expected, likely days off not entered. ${days}`;
  if (gap > 0) return `${plural(gap, "day")} fewer than expected, but not a whole or half number of days at the rate. ${days}`;
  if (gap < 0) return `${plural(-gap, "day")} more than expected. ${days}`;
  return `The amount differs from the expected ${money(row.expected)}`;
}

function vatTip(vat: NonNullable<ConsultingMonth["vat"]>): string {
  const paid = vat.paid.map((p) => `${money(p.amount)} on ${p.date}`).join(", ");
  if (vat.status === "paid") return `Paid ${paid}`;
  if (vat.status === "differs") return `Paid ${paid}; ${money(vat.expected)} expected by ${vat.due}`;
  if (vat.status === "missing") return `No VAT payment found; ${money(vat.expected)} was due ${vat.due} (or not imported yet)`;
  return `${money(vat.expected)} due ${vat.due}`;
}

/** How long a late payment or missing VAT stays in Outlook; older ones remain in the Payments table as history. */
const RECENT_DAYS = 60;

/** What needs attention in the check, as one text each for Outlook, or none: only what fell due recently. */
function consultingWarnings(check: ConsultingCheck | null, now = Date.now()): string[] {
  if (!check) return [];
  const recent = (due: string) => Date.parse(due) >= now - RECENT_DAYS * 86_400_000;
  const late = check.months.filter((row) => row.status === "late" && recent(row.due));
  const missing = check.months.filter((row) => row.vat?.status === "missing" && recent(row.vat.due));
  const names = (rows: ConsultingMonth[]) => rows.map((row) => monthLabel(row.month)).reverse().join(", ");
  const warnings = [];
  if (late.length) warnings.push(`${plural(late.length, "consulting payment")} late (${names(late)})`);
  if (missing.length) warnings.push(`${plural(missing.length, "VAT payment")} missing (${names(missing)}), or not imported yet`);
  return warnings;
}

function ConsultingPayments({ check }: { check: ConsultingCheck }) {
  const { summary } = check;
  const parts = [
    summary.late > 0 && `${summary.late} late`,
    summary.differs > 0 && `${summary.differs} ${summary.differs === 1 ? "differs" : "differ"}`,
    summary.fewer_days > 0 && `${summary.fewer_days} with fewer days (likely days off)`,
    summary.vat_missing > 0 && `${summary.vat_missing} VAT missing`,
  ].filter(Boolean);
  const tag = (label: string, tone: string, tip: string) => <span className={`pay-tag ${tone}`} title={tip}>{label}</span>;
  return (
    <>
      <h5 className="plan-subheading">Payments</h5>
      <p className="plan-line">{parts.length ? parts.join(", ") : "All payments as expected"}</p>
      {check.months.length > 0 && (
        <div className="table-scroll">
          <table className="rules-table plan-table" aria-label="Consulting payments">
            <thead><tr><th>Month</th><th className="numeric">Days</th><th className="numeric">Expected</th><th>Received</th><th>Status</th></tr></thead>
            <tbody>
              {[...check.months].reverse().map((row) => (
                <tr key={row.month}>
                  <td className="nowrap">{monthLabel(row.month)}</td>
                  <td className="numeric nowrap" title={`${row.working_days} working days, ${row.days_off} off`}>
                    {row.days_expected}{row.days_off > 0 ? ` (−${row.days_off})` : ""}
                  </td>
                  <td className="numeric nowrap">{money(row.expected)}</td>
                  <td className="nowrap">
                    {row.received.length ? row.received.map((r) => <div key={r.transaction_id}>{r.date} · {money(r.amount)}</div>) : "—"}
                  </td>
                  <td>
                    <div className="pay-tags">
                      {tag(PAYMENT_TAGS[row.status].label, PAYMENT_TAGS[row.status].tone, paymentTip(row))}
                      {row.vat && tag(VAT_TAGS[row.vat.status].label, VAT_TAGS[row.vat.status].tone, vatTip(row.vat))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">Until mid-2026 VAT was paid as round advances plus quarterly settlements, so older months show VAT as differs or missing.</p>
    </>
  );
}

export function PlanView({
  accountType,
  categories,
  forecast,
  onChanged,
  onNotice,
  onError,
  onClose,
  newItemSignal = 0,
  recurring,
}: {
  accountType: AccountType;
  categories: Category[];
  /** The entity's forecast, which App reloads after every change made here. */
  forecast: Forecast | null;
  onChanged: () => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onClose: () => void;
  /** Bumped by N: opens the form to add a planned item. */
  newItemSignal?: number;
  /** The recurring payments, shown on the Forecast page right after the outlook. */
  recurring?: React.ReactNode;
}) {
  const [settings, setSettings] = useState<ForecastSettings | null>(null);
  const [planned, setPlanned] = useState<PlannedItem[]>([]);
  const [linkable, setLinkable] = useState<PlannedItem[]>([]);
  const [salary, setSalary] = useState<SalarySchedule[]>([]);
  const [rates, setRates] = useState<DayRate[]>([]);
  const [daysOff, setDaysOff] = useState<DayOff[]>([]);
  const [consulting, setConsulting] = useState<ConsultingCheck | null>(null);
  const [tax, setTax] = useState<CorporateTaxYear[] | null>(null);
  const [loan, setLoan] = useState<DirectorLoan | null>(null);
  const [busy, setBusy] = useState(false);
  /** The inline message of each section that failed a change. */
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showRoles, setShowRoles] = useState(false);
  const [newRate, setNewRate] = useState({ date: "", rate: "" });
  const [newOff, setNewOff] = useState({ start: "", end: "", half: false, note: "", repeat: 0, until: "" });

  const hasSalary = accountType !== "kids";
  const isBusiness = accountType === "business";

  const load = useCallback(async () => {
    const others = ACCOUNT_TYPES.filter((kind) => kind !== accountType);
    try {
      const [nextSettings, nextPlanned, otherPlanned, nextSalary, nextRates, nextOff, nextConsulting, nextTax, nextLoan] = await Promise.all([
        api<ForecastSettings>("/api/forecast/settings"),
        api<PlannedItem[]>(`/api/planned?account_type=${accountType}`),
        Promise.all(others.map((kind) => api<PlannedItem[]>(`/api/planned?account_type=${kind}`))),
        hasSalary ? api<SalarySchedule[]>("/api/forecast/salary") : Promise.resolve([]),
        isBusiness ? api<DayRate[]>("/api/forecast/day-rates") : Promise.resolve([]),
        isBusiness ? api<DayOff[]>("/api/forecast/days-off") : Promise.resolve([]),
        // A failing request (an older server) shows no check.
        isBusiness ? api<ConsultingCheck>("/api/forecast/consulting").catch(() => null) : Promise.resolve(null),
        isBusiness ? api<CorporateTax>("/api/forecast/corporate-tax").catch(() => null) : Promise.resolve(null),
        hasSalary ? api<DirectorLoan>("/api/forecast/director-loan").catch(() => null) : Promise.resolve(null),
      ]);
      setSettings(nextSettings);
      setPlanned(nextPlanned);
      setLinkable(otherPlanned.flat());
      setSalary(nextSalary);
      setRates(nextRates);
      setDaysOff(nextOff);
      setConsulting(nextConsulting && Array.isArray(nextConsulting.months) ? nextConsulting : null);
      setTax(nextTax && Array.isArray(nextTax.years) ? nextTax.years : null);
      setLoan(nextLoan && Array.isArray(nextLoan.years) ? nextLoan : null);
    } catch (cause) {
      onError(errorMessage(cause, "Could not load the plan"));
    }
  }, [accountType, hasSalary, isBusiness, onError]);
  useEffect(() => {
    void load();
  }, [load]);

  const startAdding = () => {
    setEditingId(null);
    setErrors((current) => ({ ...current, planned: "" }));
    setAdding(true);
  };
  useSignal(newItemSignal, startAdding);

  /** Runs a change for a section, reloads what the view shows and tells App to reload the forecast. */
  const run = async (section: string, action: () => Promise<void>, failure: string) => {
    setBusy(true);
    setErrors((current) => ({ ...current, [section]: "" }));
    onError("");
    try {
      await action();
      await load();
      onChanged();
    } catch (cause) {
      setErrors((current) => ({ ...current, [section]: errorMessage(cause, failure) }));
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async (changes: Record<string, unknown>) => {
    await api("/api/forecast/settings", { method: "PUT", json: changes });
    await load();
    onChanged();
    onNotice("Saved the settings.");
  };

  // Planned items
  const link = async (id: number, linkId: string) =>
    api(`/api/planned/${id}/link`, {
      method: "PUT",
      json: { account_type: accountType, linked_item_id: linkId === "" ? null : Number(linkId) },
    });
  const savePlanned = (item: PlannedItem | null, draft: PlannedDraft) =>
    run("planned", async () => {
      const body = {
        account_type: accountType,
        date: draft.date,
        amount: draft.amount,
        category_id: draft.categoryId === "" ? null : Number(draft.categoryId),
        note: draft.note,
      };
      const saved = item
        ? await api<PlannedItem>(`/api/planned/${item.id}`, { method: "PATCH", json: body })
        : await api<PlannedItem>("/api/planned", { method: "POST", json: body });
      if (draft.linkId !== (item?.linked ? String(item.linked.id) : "")) await link(saved.id, draft.linkId);
      setAdding(false);
      setEditingId(null);
      onNotice(item ? "Saved the planned item." : "Added the planned item.");
    }, "Could not save the planned item");
  const deletePlanned = (item: PlannedItem) =>
    run("planned", async () => {
      await api(`/api/planned/${item.id}?account_type=${accountType}`, { method: "DELETE" });
      onNotice("Removed the planned item.");
    }, "Could not remove the planned item");

  // Salary, day rates and days off
  const saveSalary = (year: string, amount: string, lastMonth: number, minimum: string) =>
    run("salary", async () => {
      await api(`/api/forecast/salary/${year}`, {
        method: "PUT", json: { monthly_amount: amount, last_month: lastMonth, minimum_amount: minimum || null },
      });
      onNotice(`Saved the ${year} salary.`);
    }, "Could not save the salary");
  const deleteSalary = (year: string) =>
    run("salary", async () => {
      await api(`/api/forecast/salary/${year}`, { method: "DELETE" });
      onNotice(`Removed the ${year} salary.`);
    }, "Could not remove the salary");
  const addRate = () =>
    run("rates", async () => {
      await api(`/api/forecast/day-rates/${newRate.date}`, { method: "PUT", json: { rate: newRate.rate } });
      setNewRate({ date: "", rate: "" });
      onNotice("Saved the day rate.");
    }, "Could not save the day rate");
  const deleteRate = (rate: DayRate) =>
    run("rates", async () => {
      await api(`/api/forecast/day-rates/${rate.effective_date}`, { method: "DELETE" });
      onNotice("Removed the day rate.");
    }, "Could not remove the day rate");
  const addDayOff = () =>
    run("daysOff", async () => {
      await api("/api/forecast/days-off", {
        method: "POST",
        json: {
          start_date: newOff.start, end_date: newOff.end, fraction: newOff.half ? 0.5 : 1, note: newOff.note,
          repeat_weeks: newOff.repeat, repeat_until: newOff.repeat && newOff.until ? newOff.until : null,
        },
      });
      setNewOff({ start: "", end: "", half: false, note: "", repeat: 0, until: "" });
      onNotice("Added the days off.");
    }, "Could not add the days off");
  const deleteDayOff = (item: DayOff) =>
    run("daysOff", async () => {
      await api(`/api/forecast/days-off/${item.id}`, { method: "DELETE" });
      onNotice("Removed the days off.");
    }, "Could not remove the days off");

  const shortMonths = forecast?.months.filter((month) => month.flags.includes("transfer_short")) ?? [];
  const derivedShare = forecast?.assumptions.map((text) => SALARY_SHARE.exec(text)?.[1]).find(Boolean);
  const roles = useMemo(
    () => (Object.keys(ROLES) as ForecastRole[]).filter((role) => ROLES[role].entity === accountType),
    [accountType],
  );

  const salaryFields: Field[] = useMemo(() => [{
    kind: "number", key: "salary_net_percent", label: "Share of the salary Personal receives (%)",
  }], []);
  const consultingFields: Field[] = useMemo(() => [
    { kind: "number", key: "payment_delay_months", label: "Payment delay (months)", help: "How long after the month of work an invoice is paid" },
    { kind: "number", key: "vat_rate", label: "VAT rate (%)" },
    { kind: "number", key: "vat_delay_months", label: "VAT paid after (months)" },
    { kind: "number", key: "vat_day", label: "VAT paid on day" },
  ], []);
  const balanceFields: Field[] = useMemo(() => [
    { kind: "text", key: "personal_min_balance", label: "Personal minimum balance", help: "Business transfers money to Personal to stay above it" },
    { kind: "text", key: "business_buffer", label: "Business buffer", help: "Business keeps at least this much when it transfers" },
    { kind: "number", key: "reimbursement_delay_days", label: "Reimbursement delay (days)", help: "How long after a claim arises Business is expected to repay it" },
  ], []);
  const baselineFields: Field[] = useMemo(() => [
    { kind: "number", key: "baseline_months", label: "Months of history" },
    { kind: "checkbox", key: "baseline_weighted", label: "Weighted: recent months count more" },
    { kind: "number", key: "one_off_factor", label: "One-off factor", help: "A transaction this many times the typical month is a one-off and left out" },
    { kind: "closed", label: "Closed categories: left out of the baseline" },
  ], []);
  const taxFields: Field[] = useMemo(() => [
    { kind: "text", key: "tax_reduced_threshold", label: "Director remuneration for the reduced rate", help: "The year's remuneration must reach this" },
    { kind: "number", key: "tax_reduced_rate", label: "Reduced rate (%)" },
    { kind: "text", key: "tax_reduced_band", label: "Reduced rate applies to the first" },
    { kind: "number", key: "tax_standard_rate", label: "Standard rate (%)" },
    { kind: "number", key: "tax_base_rate", label: "Base rate for prepayments (%)", help: "Set every year; the credits and the surcharge follow from it" },
    { kind: "checkbox", key: "tax_surcharge_applies", label: "The surcharge applies" },
    { kind: "excluded", label: "Left out of the profit, besides VAT, corporate tax and transfers" },
  ], []);
  const loanFields: Field[] = useMemo(() => [
    { kind: "text", key: "loan_start", label: "Loan starts on", help: "Entity transfers from this date count as the loan" },
    { kind: "number", key: "loan_interest_rate", label: "Interest rate (%)" },
  ], []);
  const roleFields: Field[] = useMemo(() => [{ kind: "roles", roles }], [roles]);

  const editing = planned.find((item) => item.id === editingId) ?? null;
  const backtest = forecast?.backtest;
  const warnings = consultingWarnings(consulting);

  return (
    <div className="plan-view">
      <section className="panel plan-panel" aria-label="Outlook">
        <div className="section-heading">
          <div><h3>Forecast</h3><div className="muted small">What the forecast assumes, for {ACCOUNT_TYPE_LABELS[accountType]}</div></div>
          <div className="transaction-filters">
            <button className="icon-button" aria-label="Close forecast" onClick={onClose}>×</button>
          </div>
        </div>
        <h4 className="plan-heading">Outlook</h4>
        {forecast ? (
          <>
            <ul className="plan-list">
              {forecast.year_ends.map((end) => (
                <li key={end.year}>
                  End of {end.year}: <strong>{money(end.closing)}</strong>
                  <span className="muted"> (range {money(end.low)} – {money(end.high)})</span>
                </li>
              ))}
            </ul>
            <p className="plan-line">
              <span className="muted">Balances as of </span>
              {forecast.accounts.length ? forecast.accounts.map((account) => `${account.name} ${account.as_of}`).join(", ") : "—"}
            </p>
            <p className="plan-line">
              <span className="muted">Months with a transfer short: </span>
              {shortMonths.length ? shortMonths.map((month) => monthLabel(month.month)).join(", ") : "none"}
            </p>
            {warnings.map((text) => <p className="plan-line plan-warning" key={text}>{text}</p>)}
            <h5 className="plan-subheading">Assumptions</h5>
            {forecast.assumptions.length ? (
              <ul className="plan-list">{forecast.assumptions.map((text) => <li key={text}>{text}</li>)}</ul>
            ) : <p className="muted small">None beyond the settings below.</p>}
          </>
        ) : <p className="muted small">The forecast is not available.</p>}
      </section>

      {recurring}

      <section className="panel plan-panel" aria-label="Planned items">
        <div className="plan-heading-row">
          <h4 className="plan-heading">Planned items <span className="count">{planned.length}</span></h4>
          <button className="button secondary" onClick={startAdding}>Add planned item</button>
        </div>
        <p className="muted small">One-off expected income or expenses, such as a tax payment.</p>
        {adding && (
          <PlannedForm
            label="Add planned item"
            initial={emptyPlanned}
            categories={categories}
            linkable={linkable}
            busy={busy}
            error={errors.planned ?? ""}
            onSave={(draft) => void savePlanned(null, draft)}
            onCancel={() => setAdding(false)}
          />
        )}
        {!adding && !editing && errors.planned && <p className="recurring-error" role="alert">{errors.planned}</p>}
        {planned.length > 0 ? (
          <div className="table-scroll">
            <table className="rules-table plan-table" aria-label="Planned items">
              <thead>
                <tr><th>Date</th><th className="numeric">Amount</th><th>Category</th><th>Note</th><th>Linked</th><th /></tr>
              </thead>
              <tbody>
                {planned.map((item) => item.id === editingId ? (
                  <tr key={item.id}>
                    <td colSpan={6}>
                      <PlannedForm
                        label="Edit planned item"
                        initial={plannedDraftOf(item)}
                        categories={categories}
                        linkable={linkable}
                        busy={busy}
                        error={errors.planned ?? ""}
                        onSave={(draft) => void savePlanned(item, draft)}
                        onCancel={() => setEditingId(null)}
                      />
                    </td>
                  </tr>
                ) : (
                  <tr key={item.id}>
                    <td className="nowrap">{item.date}</td>
                    <td className={`numeric ${signClass(item.amount)}`}>{money(item.amount)}</td>
                    <td className="truncate-cell">{item.category ?? "—"}</td>
                    <td className="truncate-cell" title={item.note}>{item.note || "—"}</td>
                    <td className="truncate-cell">
                      {item.linked
                        ? `${ACCOUNT_TYPE_LABELS[item.linked.account_type]} · ${item.linked.date} · ${money(item.linked.amount)}`
                        : "—"}
                    </td>
                    <td className="rules-actions">
                      <button className="button secondary" aria-label={`Edit planned item ${item.date}`} disabled={busy} onClick={() => { setAdding(false); setEditingId(item.id); }}>Edit</button>
                      <button className="button secondary" aria-label={`Delete planned item ${item.date}`} disabled={busy} onClick={() => void deletePlanned(item)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted small">Nothing planned.</p>}
      </section>

      {hasSalary && (
        <ReimbursementsSection accountType={accountType} categories={categories} onChanged={onChanged} onNotice={onNotice} />
      )}

      {isBusiness && settings && (
        <CorporateTaxSection
          tax={tax}
          settings={settings}
          planned={planned}
          today={forecast?.today ?? new Date().toLocaleDateString("sv")}
          onChanged={async () => { await load(); onChanged(); }}
          onNotice={onNotice}
          rules={(
            <SettingsSection
              fields={taxFields}
              settings={settings}
              categories={categories.filter((category) => category.id !== settings.roles.vat
                && category.id !== settings.roles.corporate_tax && category.id !== settings.roles.transfer_business)}
              accountType={accountType}
              onSave={saveSettings}
            />
          )}
        />
      )}

      {hasSalary && settings && (
        <DirectorLoanSection
          loan={loan}
          onLoan={setLoan}
          onChanged={onChanged}
          onNotice={onNotice}
          rules={(
            <SettingsSection fields={loanFields} settings={settings} categories={categories} accountType={accountType} onSave={saveSettings} />
          )}
        />
      )}

      {/* The settings sections flow in columns, so a short one does not leave a gap beside a tall one. */}
      <div className="plan-columns">
      {hasSalary && settings && (
        <section className="panel plan-panel" aria-label="Salary">
          <h4 className="plan-heading">Salary</h4>
          <p className="muted small">The monthly salary Business pays, up to and including the last month, then the minimum each month after it (what reaches Personal, so the net share doesn't apply). A year without a schedule repeats the latest earlier one.</p>
          <div className="table-scroll">
            <table className="rules-table plan-table" aria-label="Salary schedules">
              <thead><tr><th>Year</th><th>Monthly amount</th><th>Last month</th><th title="Paid each month after the last month, as it reaches Personal">Minimum after</th><th /></tr></thead>
              <tbody>
                {salary.map((schedule) => (
                  <SalaryRow
                    key={`${schedule.year}\u0000${schedule.monthly_amount}\u0000${schedule.last_month}`}
                    schedule={schedule}
                    busy={busy}
                    onSave={saveSalary}
                    onDelete={() => void deleteSalary(schedule.year)}
                  />
                ))}
                <SalaryRow key={`new\u0000${salary.length}`} schedule={null} busy={busy} onSave={saveSalary} />
              </tbody>
            </table>
          </div>
          {errors.salary && <p className="recurring-error" role="alert">{errors.salary}</p>}
          <SettingsSection fields={salaryFields} settings={settings} categories={categories} accountType={accountType} onSave={saveSettings}>
            <p className="muted small">
              0 takes the share from the last 12 months of history
              {derivedShare ? <>; currently <strong>{derivedShare}%</strong></> : null}.
            </p>
          </SettingsSection>
        </section>
      )}

      {isBusiness && settings && (
        <section className="panel plan-panel" aria-label="Consulting">
          <h4 className="plan-heading">Consulting</h4>
          {consulting && <ConsultingPayments check={consulting} />}
          <h5 className="plan-subheading">Day rates</h5>
          <div className="table-scroll">
            <table className="rules-table plan-table" aria-label="Day rates">
              <thead><tr><th>Effective from</th><th>Rate</th><th /></tr></thead>
              <tbody>
                {rates.map((rate) => (
                  <tr key={rate.effective_date}>
                    <td className="nowrap">{rate.effective_date}</td>
                    <td>{money(rate.rate)}</td>
                    <td className="rules-actions">
                      <button className="button secondary" aria-label={`Delete day rate ${rate.effective_date}`} disabled={busy} onClick={() => void deleteRate(rate)}>Delete</button>
                    </td>
                  </tr>
                ))}
                <tr>
                  <td><input type="date" aria-label="New day rate date" value={newRate.date} onChange={(event) => setNewRate({ ...newRate, date: event.target.value })} /></td>
                  <td><input type="text" inputMode="decimal" aria-label="New day rate" placeholder="Rate per day" value={newRate.rate} onChange={(event) => setNewRate({ ...newRate, rate: event.target.value })} /></td>
                  <td className="rules-actions"><button className="button secondary" disabled={busy} onClick={() => void addRate()}>Add day rate</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          {errors.rates && <p className="recurring-error" role="alert">{errors.rates}</p>}
          <h5 className="plan-subheading">Days off</h5>
          <div className="table-scroll">
            <table className="rules-table plan-table" aria-label="Days off">
              <thead><tr><th>Start</th><th>End</th><th>Half days</th><th>Repeats</th><th>Note</th><th /></tr></thead>
              <tbody>
                {daysOff.map((item) => (
                  <tr key={item.id}>
                    <td className="nowrap">{item.start_date}</td>
                    <td className="nowrap">{item.end_date}</td>
                    <td>{item.fraction === 0.5 ? "Yes" : "No"}</td>
                    <td className="nowrap">{repeatLabel(item)}</td>
                    <td className="truncate-cell" title={item.note}>{item.note || "—"}</td>
                    <td className="rules-actions">
                      <button className="button secondary" aria-label={`Delete days off ${item.start_date}`} disabled={busy} onClick={() => void deleteDayOff(item)}>Delete</button>
                    </td>
                  </tr>
                ))}
                <tr>
                  <td><input type="date" aria-label="New day off start" value={newOff.start} onChange={(event) => setNewOff({ ...newOff, start: event.target.value })} /></td>
                  <td><input type="date" aria-label="New day off end" value={newOff.end} onChange={(event) => setNewOff({ ...newOff, end: event.target.value })} /></td>
                  <td><input type="checkbox" aria-label="New day off is half days" checked={newOff.half} onChange={(event) => setNewOff({ ...newOff, half: event.target.checked })} /></td>
                  <td className="day-off-repeat">
                    <select aria-label="New day off repeats" value={newOff.repeat} onChange={(event) => setNewOff({ ...newOff, repeat: Number(event.target.value) })}>
                      {REPEAT_CHOICES.map(([weeks, label]) => <option key={weeks} value={weeks}>{label}</option>)}
                    </select>
                    {newOff.repeat > 0 && (
                      <input type="date" aria-label="New day off repeats until" title="Last repeat on or before this date; empty: for good" value={newOff.until} onChange={(event) => setNewOff({ ...newOff, until: event.target.value })} />
                    )}
                  </td>
                  <td><input type="text" aria-label="New day off note" value={newOff.note} onChange={(event) => setNewOff({ ...newOff, note: event.target.value })} /></td>
                  <td className="rules-actions"><button className="button secondary" disabled={busy} onClick={() => void addDayOff()}>Add days off</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          {errors.daysOff && <p className="recurring-error" role="alert">{errors.daysOff}</p>}
          <h5 className="plan-subheading">Invoicing and VAT</h5>
          <SettingsSection fields={consultingFields} settings={settings} categories={categories} accountType={accountType} onSave={saveSettings} />
        </section>
      )}

      {hasSalary && settings && (
        <section className="panel plan-panel" aria-label="Balances">
          <h4 className="plan-heading">Balances</h4>
          <SettingsSection fields={balanceFields} settings={settings} categories={categories} accountType={accountType} onSave={saveSettings} />
        </section>
      )}

      {settings && (
        <section className="panel plan-panel" aria-label="Baselines">
          <h4 className="plan-heading">Baselines</h4>
          <p className="muted small">Everything else is projected from a typical month of each category.</p>
          {/* The models' own categories never get a baseline, so closing them would change nothing. */}
          <SettingsSection
            fields={baselineFields}
            settings={settings}
            categories={categories.filter((category) => !Object.values(settings.roles).includes(category.id))}
            accountType={accountType}
            onSave={saveSettings}
          />
          {backtest && (
            <p className="plan-line" aria-label="Backtest">
              <span className="muted">Backtest: </span>
              {backtest.origins === 0 ? "not enough history yet" : (
                <>
                  {backtest.origins} origins; recommended {backtest.recommended.baseline_months} months
                  {backtest.recommended.baseline_weighted ? ", weighted" : ", not weighted"}
                  {backtest.horizons.length > 0 && "; p10 to p90 "}
                  {backtest.horizons.map((horizon) => `${horizon.months} mo ${tipMoney(Number(horizon.p10))} to ${tipMoney(Number(horizon.p90))}`).join(", ")}
                </>
              )}
            </p>
          )}
          <h5 className="plan-subheading">One-offs left out of the baseline</h5>
          {forecast && forecast.one_offs.length > 0 ? (
            <div className="table-scroll">
              <table className="rules-table plan-table" aria-label="One-offs">
                <thead><tr><th>Date</th><th>Merchant</th><th>Category</th><th className="numeric">Amount</th></tr></thead>
                <tbody>
                  {forecast.one_offs.map((item) => (
                    <tr key={item.transaction_id}>
                      <td className="nowrap">{item.date}</td>
                      <td className="truncate-cell" title={item.merchant}>{item.merchant}</td>
                      <td className="truncate-cell">{item.category}</td>
                      <td className={`numeric ${signClass(item.amount)}`}>{money(item.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="muted small">No one-offs in the baseline's history.</p>}
        </section>
      )}

      {settings && roles.length > 0 && (
        <section className="panel plan-panel" aria-label="Categories used by the models">
          <h4 className="plan-heading">
            <button className="recurring-toggle" aria-expanded={showRoles} onClick={() => setShowRoles((shown) => !shown)}>
              {showRoles ? "Hide" : "Show"} categories used by the models
            </button>
          </h4>
          {showRoles && (
            <SettingsSection fields={roleFields} settings={settings} categories={categories} accountType={accountType} onSave={saveSettings} />
          )}
        </section>
      )}
      </div>
    </div>
  );
}
