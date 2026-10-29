import { ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS, type AccountType } from "./types";

/** The overview, or a full-page view that replaces it. */
export type View = "overview" | "triage" | "forecast" | "rules" | "categories" | "accounts";
/** The parts of the pages that hold two: the Forecast page (plan, recurring) and the Accounts page (accounts, backups). */
export type Section = "plan" | "recurring" | "accounts" | "backups";

const VIEW_BUTTONS: { view: Exclude<View, "overview">; label: string }[] = [
  { view: "triage", label: "Triage" },
  { view: "forecast", label: "Forecast" },
  { view: "categories", label: "Categories" },
  { view: "rules", label: "Rules" },
  { view: "accounts", label: "Accounts" },
];

export function TopBar({
  accountType,
  onAccountType,
  years,
  year,
  onYear,
  forecastYears = [],
  showForecast = true,
  onToggleForecast,
  onAccounts,
  view,
  onTriage,
  onRules,
  onCategories,
  onForecast,
}: {
  accountType: AccountType;
  onAccountType: (accountType: AccountType) => void;
  years: string[];
  year: string;
  onYear: (year: string) => void;
  /** Years that exist only as a forecast; their tabs say so. */
  forecastYears?: string[];
  /** Whether projections show; the switch beside the title turns them on or off. */
  showForecast?: boolean;
  onToggleForecast?: () => void;
  onAccounts: () => void;
  view: View;
  onTriage: () => void;
  onRules: () => void;
  onCategories: () => void;
  onForecast: () => void;
}) {
  const handlers = { triage: onTriage, forecast: onForecast, categories: onCategories, rules: onRules, accounts: onAccounts };
  return (
    <header className="topbar">
      <div className="brand">
        <h1>Finance overview</h1>
        {onToggleForecast && (
          <button
            type="button"
            className="forecast-switch"
            role="switch"
            aria-checked={showForecast}
            title="Show the forecast in the year table and the balance graph"
            onClick={onToggleForecast}
          >
            <span className="forecast-switch-track" aria-hidden="true"><span className="forecast-switch-thumb" /></span>
            Forecast
          </button>
        )}
      </div>
      <div className="topbar-controls">
        <nav className="account-tabs" aria-label="Finance type">
          {ACCOUNT_TYPES.map((kind) => (
            <button
              className={accountType === kind ? "account-tab active" : "account-tab"}
              key={kind}
              aria-pressed={accountType === kind}
              onClick={() => onAccountType(kind)}
            >
              {ACCOUNT_TYPE_LABELS[kind]}
            </button>
          ))}
        </nav>
        {years.length > 0 && (
          <nav className="account-tabs year-tabs" aria-label="Report year">
            {years.map((value) => (
              <button
                className={value === year ? "account-tab active" : "account-tab"}
                key={value}
                aria-pressed={value === year}
                title={forecastYears.includes(value) ? `${value} · forecast` : undefined}
                onClick={() => onYear(value)}
              >
                {value}
              </button>
            ))}
          </nav>
        )}
      </div>
      <nav className="account-tabs topbar-actions" aria-label="Views">
        {VIEW_BUTTONS.map(({ view: target, label }) => (
          <button
            className={view === target ? "account-tab active" : "account-tab"}
            key={target}
            aria-pressed={view === target}
            onClick={handlers[target]}
          >
            {label}
          </button>
        ))}
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer>
      <span>Stored locally on this computer · No cloud sync or external requests</span>
      <span><kbd>K</kbd> keyboard shortcuts</span>
    </footer>
  );
}
