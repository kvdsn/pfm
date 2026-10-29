export const ACCOUNT_TYPES = ["personal", "business", "kids"] as const;
export type AccountType = (typeof ACCOUNT_TYPES)[number];

export const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = {
  personal: "Personal",
  business: "Business",
  kids: "Kids",
};
export type FlowType = "income" | "expense" | "transfer";
/** How a rule's text matches a field: anywhere in it, as a whole word, or as the whole field. */
export type MatchMode = "contains" | "word" | "exact";
export type CategorySummary = {
  category_id: number | null;
  flow_type: FlowType;
  name: string;
  amount: string;
  transaction_count: number;
};
/** A year-overview row filtering the transactions: a category within a flow, or own-account transfers. */
export type SelectedCategory = Pick<CategorySummary, "category_id" | "name"> & { flow_type: FlowType | "own_transfer" };
/** `is_income` is absent from servers that predate income categories, which is the same as false. */
export type Category = { id: number; name: string; rule_flow_type: FlowType | null; is_income?: boolean };
export type RuleSample = {
  id: number;
  date: string;
  merchant: string;
  description: string;
  amount: string | null;
};
/** Signals that a saved rule may need attention; see `RuleHealthInfo`. */
export type RuleHealth = "idle" | "shadowed" | "broad";
export type RuleHealthInfo = {
  /** Absent from older servers, which is the same as none. */
  health?: RuleHealth[];
  /** For `shadowed`: the earlier rules deciding most of this rule's matches, most first (up to three). */
  shadowed_by?: { id: number; match_text: string; count: number }[];
};
export type CategoryRule = RuleHealthInfo & {
  id: number;
  category_id: number;
  match_text: string;
  match_fields: ("merchant" | "description" | "memo")[];
  legacy_normalized: boolean;
  match_mode: MatchMode;
  flow_type: FlowType;
  priority: number;
  match_count: number;
  first_match_count: number;
  /** Matches that are card settlements replaced by their statement; absent from older servers. */
  itemized_count?: number;
  samples: RuleSample[];
};
export type ManagedCategory = Pick<Category, "id" | "name" | "is_income"> & {
  transaction_count: number;
  rules: CategoryRule[];
};
export type RulePreview = { match_count: number; first_match_count: number; itemized_count?: number; samples: RuleSample[] };
/** What a rule would do: one row per matching transaction, newest first. */
export type RuleImpact = {
  match_count: number;
  /** Matches that are card settlements replaced by their statement; absent from older servers. */
  itemized_count?: number;
  /** Matches no earlier rule claims, which this rule categorizes. */
  win_count: number;
  /** Decided matches whose category or flow type would change when the rule is applied; those set by hand never do. */
  change_count: number;
  /** Per field, how many transactions contain the text there. */
  field_counts: Record<"merchant" | "description" | "memo", number>;
  matches: {
    id: number;
    date: string;
    amount: string;
    merchant: string;
    description: string;
    memo: string;
    category: string;
    /** The text of the earlier rule that claims this transaction, if any. */
    decided_by: string | null;
    changes: boolean;
    /** Set by hand: the rule decides it, but applying leaves it as it is. */
    manual: boolean;
  }[];
  truncated: boolean;
  /** Transactions the saved rule categorized that the draft no longer decides. */
  released: {
    id: number;
    date: string;
    amount: string;
    merchant: string;
    description: string;
    memo: string;
    category: string;
    /** Where applying moves it: the next matching rule's category, or Uncategorized. */
    new_category: string;
    /** The text of that next matching rule, if any. */
    decided_by: string | null;
  }[];
  released_count: number;
};
export type ImportRecord = {
  id: number;
  filename: string;
  imported_at: string;
  row_count: number;
  inserted_count: number;
  duplicate_count: number;
  transaction_count: number;
  masked_iban: string;
  account_label: string;
  /** Transactions of this import with a note or follow-up. Absent when the server predates the field. */
  annotated?: number;
};
/** Last year's month of this year's latest transaction, cut off at the same day (`through`). */
export type PartialSummary = Summary & { through: string };

export type Summary = {
  month: string;
  income: string;
  expenses: string;
  net: string;
  own_transfers: string;
  categories: CategorySummary[];
  account_balances: { account: string; opening: string; closing: string }[];
  transaction_count: number;
};
export type BalancePoint = { date: string; balance: string };
export type BalanceHistory = { points: BalancePoint[] };
export type Transaction = {
  id: number;
  booking_date: string;
  value_date: string;
  amount: string;
  balance: string | null;
  currency: string;
  /** The counterparty, or the merchant the bank text mentions. */
  merchant: string;
  /** One short line of useful detail extracted from the bank texts. */
  details: string;
  /** The bank's original texts. */
  description: string;
  memo: string;
  counterparty: string;
  counterparty_iban: string;
  flow_type: FlowType;
  category_id: number | null;
  category: string | null;
  /** How the category was set: by a rule, by hand, or by default (no rule matched). */
  category_source: "rule" | "manual" | "default";
  /** The text of the rule that set the category, when the source is a rule. */
  category_rule: string | null;
  /** The id of that rule, when it still exists. */
  category_rule_id: number | null;
  /** That rule's 1-based place in its account type's priority order, as numbered in the rules view. */
  category_rule_position: number | null;
  /** The source account, such as "KBC" or "Belfius card". */
  account: string;
  /** A transfer to or from another account of the same account type; not counted in totals. */
  own_transfer: boolean;
  /** A card settlement replaced by its statement's purchases; not counted in totals. */
  itemized: boolean;
  /** The user's free-text note; empty when there is none. */
  note: string;
  /** No follow-up, one still to do, or one done. */
  follow_up: FollowUp | null;
  /** When the follow-up was last opened or marked done (ISO). */
  follow_up_at: string | null;
};
/** A transaction's follow-up state. */
export type FollowUp = "open" | "done";
/** Which transactions the table lists: the selected year, or the follow-ups of all years. */
export type FollowUpFilter = "all" | FollowUp;
/** What the annotation endpoint returns for one transaction. */
export type Annotation = { note: string; follow_up: FollowUp | null; follow_up_at: string | null };
export type ImportPreview = {
  token: string;
  filename: string;
  masked_iban: string;
  account_label: string;
  account_type: AccountType | null;
  account_type_required: boolean;
  transaction_count: number;
  duplicate_count: number;
  new_count: number;
  unknown_columns: string[];
  preview: {
    date: string;
    merchant: string;
    description: string;
    memo: string;
    amount: string;
    suggestions: Record<AccountType, { category: string; flow_type: FlowType }>;
  }[];
  sample_truncated: boolean;
};
/** Other transactions with the same merchant and kind, and the category they most often have. */
export type SimilarTransactions = {
  merchant: string;
  /** The first part of the detail line, such as "Card" or "Interest". */
  kind: string;
  /** Other transactions with this merchant of any kind; more than `others` means a merchant rule would reach further. */
  merchant_count: number;
  suggestion: { category_id: number; name: string; count: number } | null;
  others: { id: number; category_id: number }[];
};
/** A snapshot of the database saved in the backup folder. */
export type Backup = { name: string; created_at: string; label: string; automatic: boolean; size_bytes: number };

/** Uncategorized transactions sharing a merchant and kind, as the triage view groups them. */
export type UncategorizedGroup = {
  /** Empty when the bank names no counterparty. */
  merchant: string;
  /** The first part of the detail line, such as "Card" or "Interest". */
  kind: string;
  count: number;
  total: string;
  first_date: string;
  last_date: string;
  transaction_ids: number[];
  transactions: { id: number; date: string; amount: string; details: string }[];
  /** Up to three distinct detail lines. */
  sample_details: string[];
  /** The category most used by categorized transactions of the same merchant and kind. */
  suggestion: { category_id: number; name: string; count: number } | null;
  /** How many transactions an exact merchant rule for this merchant would match, of any kind. */
  merchant_rule_matches: number;
};
/** One row of the accounts view: an account or card with the period its active transactions cover. */
export type AccountOverview = {
  id: number;
  account_type: AccountType;
  bank: string;
  kind: "bank" | "card";
  label: string;
  masked: string;
  transaction_count: number;
  first_date: string | null;
  last_date: string | null;
  latest_balance: string | null;
  last_import_at: string | null;
  /** The account's imports, newest first. Absent when the server predates the field. */
  imports?: ImportRecord[];
};
/** A yearly budget for a category: a spending limit, or a target for an income category. */
export type Budget = { category_id: number; year: string; amount: string };

/** Where a card statement stands: paid by a bank settlement, covered by credit, nothing due, or unpaid. */
export type StatementStatus = "paid" | "credit" | "nothing_due" | "unpaid";
export type Statement = {
  id: number;
  period: string;
  debit_date: string;
  /** The import that brought its purchases. */
  import_id: number;
  purchase_count: number;
  removed_count: number;
  /** Signed sum of the purchases, removed ones included; negative is spent. */
  total: string;
  /** Credit from an earlier refund-only statement (positive). */
  carried_credit: string | null;
  /** The amount left to pay (positive; "0.00" when nothing). */
  due: string;
  status: StatementStatus;
  settlement: { id: number; booking_date: string; amount: string; account: string } | null;
};
/** The statement behind an itemized card settlement, with its purchases listed like any transaction. */
export type SettlementStatement = Statement & {
  card: string;
  purchases: Transaction[];
};

export type RecurringCadence = "monthly" | "quarterly" | "yearly";
export type RecurringStatus = "confirmed" | "candidate" | "ignored";
export type RecurringFlag = "new" | "price_change" | "stopped";

export type RecurringItem = {
  /** Null for a candidate, which has no stored row yet. */
  id: number | null;
  status: RecurringStatus;
  merchant: string;
  kind: string;
  cadence: RecurringCadence;
  day_of_month: number | null;
  category_id: number | null;
  category: string | null;
  amount: string;
  monthly_amount: string;
  amounts: { effective_date: string; amount: string }[];
  start_date: string;
  end_date: string | null;
  last_date: string | null;
  next_date: string | null;
  /** The 13 calendar months ending with this month, oldest first; null for a month without transactions. */
  history: { period: string; amount: string | null }[];
  occurrences: number;
  flags: RecurringFlag[];
  linked: { id: number; account_type: AccountType; merchant: string; kind: string } | null;
  note: string;
};

export type RecurringList = {
  monthly: { income: string; expense: string; net: string };
  items: RecurringItem[];
  candidates: RecurringItem[];
  ignored: RecurringItem[];
};

/** A confirmed item of another entity that an item can be linked to. */
export type LinkableRecurringItem = {
  id: number;
  account_type: AccountType;
  merchant: string;
  kind: string;
  amount: string;
  cadence: RecurringCadence;
};

/** One projected amount in a forecast month; negative is money out. */
export type ForecastLine = {
  category_id: number;
  category: string;
  amount: string;
  source: "recurring" | "planned" | "reimbursement" | "consulting" | "vat" | "salary" | "baseline" | "transfer";
  date: string | null;
  detail: string;
};
export type ForecastMonth = {
  month: string;
  /** The current month, of which the part before today is already booked. */
  partial: boolean;
  lines: ForecastLine[];
  /** What is booked so far in the month. */
  actual: { income: string; expense: string };
  /** The whole month: booked plus projected. */
  income: string;
  expense: string;
  net: string;
  closing: string;
  low: string;
  high: string;
  flags: "transfer_short"[];
};
export type BacktestVariant = { baseline_months: number; baseline_weighted: boolean };
export type Forecast = {
  account_type: AccountType;
  today: string;
  accounts: { name: string; balance: string; as_of: string }[];
  opening: string;
  months: ForecastMonth[];
  year_ends: { year: string; closing: string; low: string; high: string }[];
  one_offs: { transaction_id: number; date: string; merchant: string; category: string; amount: string }[];
  assumptions: string[];
  backtest: {
    origins: number;
    horizons: { months: number; p10: string; p90: string; mae: string }[];
    baseline: BacktestVariant;
    recommended: BacktestVariant;
  };
};

/** The model roles whose categories the settings name; see ROLES in the backend. */
export type ForecastRole =
  | "consulting_income" | "vat" | "corporate_tax" | "salary_business" | "salary_personal"
  | "transfer_business" | "transfer_personal" | "loan_interest_business" | "loan_interest_personal";
export type ForecastSettings = {
  personal_min_balance: string;
  business_buffer: string;
  payment_delay_months: number;
  vat_rate: number;
  vat_delay_months: number;
  vat_day: number;
  baseline_months: number;
  baseline_weighted: boolean;
  one_off_factor: number;
  /** The share of the salary Personal receives; 0 derives it from history. */
  salary_net_percent: number;
  /** Days after a reimbursement claim arises that Business is expected to repay it. */
  reimbursement_delay_days: number;
  /** Corporate tax rules: money as formatted amounts, rates in percent. */
  tax_reduced_threshold: string;
  tax_reduced_rate: number;
  tax_reduced_band: string;
  tax_standard_rate: number;
  tax_base_rate: number;
  tax_surcharge_applies: boolean;
  /** Business categories left out of the tax profit, beyond the always-excluded ones. */
  tax_profit_excluded: number[];
  /** Director loan: the day Entity transfers start to count as the loan, and the simple interest rate in percent. */
  loan_start: string;
  loan_interest_rate: number;
  roles: Record<ForecastRole, number | null>;
  closed_categories: Record<AccountType, number[]>;
};
export type TaxDeadline = { date: string; prepaid: string; planned: string; advised: string; past: boolean };
/** Business's corporate tax estimate for a year; see GET /api/forecast/corporate-tax. */
export type CorporateTaxYear = {
  year: string;
  profit: string;
  eligible: boolean;
  salary: string;
  tax: string;
  breakdown: { category: string; amount: string }[];
  deadlines: TaxDeadline[];
  prepaid_total: string;
  planned_total: string;
  surcharge_as_planned: string;
  /** No deadline is left, so there is no advice. */
  closed?: boolean;
  assumptions: string[];
};
export type CorporateTax = { years: CorporateTaxYear[] };
/** A year's salary: the monthly amount up to and including `last_month`, then `minimum_amount` each month after it. */
export type SalarySchedule = { year: string; monthly_amount: string; last_month: number; minimum_amount: string };
export type DayRate = { effective_date: string; rate: string };
export type DayOff = {
  id: number;
  start_date: string;
  end_date: string;
  fraction: number;
  note: string;
  /** 0: once; otherwise the span repeats every so many weeks, until repeat_until (null: for good). */
  repeat_weeks: number;
  repeat_until: string | null;
};
export type PlannedItem = {
  id: number;
  account_type: AccountType;
  date: string;
  amount: string;
  category_id: number | null;
  category: string | null;
  note: string;
  linked: { id: number; account_type: AccountType; date: string; amount: string; note: string } | null;
};

/** A transaction as a claim or a possible repayment shows it. */
export type ClaimTransaction = { id: number; date: string; merchant: string; amount: string };
/** What Business owes Personal for a business cost paid privately; see GET /api/reimbursements. */
export type ReimbursementClaim = {
  id: number;
  date: string;
  description: string;
  amount: string;
  /** The percentage of the expense, or null for an amount entered directly. */
  share: number | null;
  source_transaction_id: number | null;
  source: ClaimTransaction | null;
  personal_category_id: number | null;
  personal_category: string | null;
  business_category_id: number | null;
  business_category: string | null;
  expected_date: string | null;
  /** When the repayment is expected: the expected date, or the claim's date plus the delay. */
  expected_on: string;
  paid_transaction_id: number | null;
  paid: ClaimTransaction | null;
  note: string;
};
export type Reimbursements = {
  claims: ReimbursementClaim[];
  open_total: string;
  paid_total_this_year: string;
  /** Personal incoming transactions from Business that could be a claim's repayment. */
  candidates: ClaimTransaction[];
};

/** One month of work in the consulting payment check; see GET /api/forecast/consulting. */
export type ConsultingMonth = {
  month: string;
  working_days: number;
  days_off: number;
  days_expected: number;
  expected: string;
  due: string;
  received: { date: string; amount: string; transaction_id: number }[];
  days_paid: number;
  status: "paid" | "fewer_days" | "differs" | "late" | "due";
  vat: {
    expected: string;
    due: string;
    paid: { date: string; amount: string }[];
    status: "paid" | "differs" | "missing" | "due";
  } | null;
};
export type ConsultingCheck = {
  months: ConsultingMonth[];
  summary: { late: number; differs: number; fewer_days: number; vat_missing: number };
};

/** One year of the director loan; see GET /api/forecast/director-loan. */
export type DirectorLoanYear = {
  year: string;
  opening: string;
  transfers: string;
  transfers_projected: string;
  closing: string;
  interest: string;
  interest_paid: string;
  interest_planned: string;
  /** Cumulative: accrued minus paid minus planned, through the end of this year. */
  interest_unpaid: string;
};
export type DirectorLoanMovement = { date: string; amount: string; projected: boolean; balance: string };
export type DirectorLoanPayment = { date: string; amount: string; projected: boolean };
export type DirectorLoan = {
  start: string;
  rate: number;
  balance: string;
  interest_to_date: string;
  interest_paid: string;
  years: DirectorLoanYear[];
  movements: DirectorLoanMovement[];
  payments: DirectorLoanPayment[];
  notes: string[];
};
