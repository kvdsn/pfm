# Forecast page: summary, tabs and closed settings

The Forecast page stacks up to 14 panels in one column (Business). Answers, checks and inputs are mixed,
and the seven settings forms take most of the height. This plan makes it read top-down: first the answer,
then whether to trust it, then what it rests on.

## 1. Summary strip

A row of tiles at the top of the page, above the tabs, so it shows on every tab:

- **End of this year:** the closing, with its range below.
- **Lowest point:** the lowest month-end closing in the horizon, with its month.
- **Room above the buffer** (Business only): this year's `room` from the money plan.
- **Needs attention:** a count; below it, one short line per item (see 5). "Nothing" when empty.

Clicking a tile opens the tab with the panel that explains it and scrolls to that panel
(year ends and lowest point: Outlook; room: Money plan; an attention item: its panel).

## 2. Three tabs

| Tab | Panels |
| --- | --- |
| **Outlook** | Outlook (year ends, balances as of, short months, consulting warnings, assumptions), Money plan, Ways to take money out, Corporate tax, Director loan |
| **Check** | Forecast vs reality, Consulting payments (the paid/late table, moved out of Consulting), Backtest and one-offs (moved out of Baselines) |
| **Inputs** | Recurring payments, Planned items, Reimbursements, Salary, Consulting (day rates, days off), Balances, Baselines (settings only), Categories used by the models |

A tab only lists the panels its entity has; Kids has no Check tab content beyond the backtest, and that's fine.

- The tab lives in App (`forecastTab`, remembered in `localStorage` as `pfm.forecastTab`) and is passed to PlanView.
- All tabs stay mounted; the others are `hidden`. This keeps forms, the recurring selection and loading as they are.
- **Keys:** ← and → switch tabs on the Forecast page (free there; on the Overview they switch years).
  N switches to Inputs before it opens the planned-item form. Switching tabs resets `section` to `plan`,
  so ↑/↓ never move through a hidden recurring list. Opening a recurring item from Forecast vs reality switches to Inputs.
- Tabs reuse the existing tab style (`.account-tab`-like), with an ARIA `tablist`.

## 3. Settings closed by default

`SettingsSection` shows a one-line summary of its saved values and an **Edit** button
(for example "Payment delay 2 months · VAT rate 21% · VAT paid after 1 month · on day 20").
Checkbox lists read as a count ("3 closed categories"), the buffer and reserve rows as a count too.
Edit opens the form as today, with Save and a new Cancel; saving closes it again.
The roles panel's own Show/Hide toggle goes, as the closed summary covers it.

## 4. Use the width

On the Outlook tab from 1400px: a two-column grid, the Outlook panel in a narrower left column
(about a third), Money plan and Ways to take money out stacked on the right; Corporate tax and
Director loan below, across. Below 1400px everything stacks as today. The Inputs tab keeps the
current two-column `plan-columns` flow. Tables must still fit at 1024px and 850px.

## 5. What needs attention

Shown in the summary's "Needs attention" tile and as a count badge on the tab that holds it:

- Months with a transfer short (Outlook).
- Consulting payments late, differing or with VAT missing (Check).
- Suggested recurring payments waiting for a decision (Inputs): RecurringView reports its count up through a callback.
- A balance more than 31 days older than today (Outlook, from `forecast.accounts[].as_of`).

## Docs and checks

- README and docs/REQUIREMENTS.md: describe the summary, the tabs and the closed settings in a few lines.
- `SHORTCUT_GROUPS`: ← → in the Forecast view group.
- AGENTS.md code map: mention the tabs under `PlanView.tsx`.
- Tests: PlanView (tabs, summary, edit/cancel of a settings section, attention items), App (← → and N switching tabs).
- `layout-check`: screenshot each tab at 1728, 1024 and 850px.
