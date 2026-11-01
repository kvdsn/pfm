# Forecast vs reality

Compare what the forecast predicted for a month with what actually happened, for Personal and Business. Kids is left out.

## Goals

- **Trust:** how far off the forecast usually is.
- **Causes:** which categories made the difference.
- **This month:** whether spending runs ahead of the forecast while the month is still going.
- **Tuning:** hints for settings that look wrong, with a link to where you change them.

## Snapshots

The forecast is computed live and never stored, so the comparison needs saved snapshots. There is no history before the feature ships.

- **When:** the first time the forecast is computed in a calendar month (`GET /api/forecast` for any entity), the app saves one snapshot covering Personal and Business. It is never replaced.
- **What a month's figure is:** the month exactly as the forecast showed it then. For the snapshot month, that is what was booked so far plus what was still projected, the same as the forecast's own `income`/`expense` for a partial month. The page shows the snapshot's date. The first run is usually early in the month, but a late snapshot still says what the app expected at that point.
- **Judged against:** each month is compared with the snapshot taken in that month.
- **Contents:** for each entity (`personal`, `business`) and each month, from the snapshot month to the end of the forecast:
  - **Balance:** the projected month-end balance (`closing`), with `low` and `high`.
  - **Lines:**
    - the projected lines: category id, name, `is_income`, amount in cents, source, date, recurring item id when there is one;
    - for the snapshot month, the transactions already booked: one line per category and day, with source `booked`.

  Only the snapshot month is compared now. The later months are kept for a later version.
- **Storage:** new tables in `SCHEMA` with `CREATE TABLE IF NOT EXISTS`:
  - `forecast_snapshots(id, month TEXT UNIQUE 'YYYY-MM', taken_at)`;
  - `forecast_snapshot_balances(snapshot_id, account_type, month, closing_cents, low_cents, high_cents)`;
  - `forecast_snapshot_lines(snapshot_id, account_type, month, category_id, category_name, is_income, amount_cents, source, day, recurring_id)`.

  Snapshots don't change with later imports or setting changes. A category merge points their lines at the target category, and a removal points them at Uncategorized, as for budgets. A database restore rolls snapshots back with everything else.
- **Reality:** always computed live from transactions, using the same figures as the year table: `COUNTED_SQL`, `report_flow_type`, and the month-end balance from `balance_history`. A late import fills in a month afterwards.

## Comparison rules

- **Sign convention:** `forecast` and `actual` are positive amounts, as the year table shows them. `difference = actual − forecast`:
  - expense: positive means more spent than forecast;
  - income: positive means more income than forecast;
  - balance: `actual − forecast` of the closing balance.
- **Category flow:** each category's income or expense side follows `report_flow_type`, so a row matches the year table's row.
- **Month status:**
  - `current`: today's month.
  - `complete`: a past month where every bank account of the entity (kind `bank`, with a transaction in the 90 days before the month's end) has an import made after the month ended.
  - `waiting`: any other past month.

  Only complete months count in the summary, misses and hints.
- **Miss** (`miss: true`):
  - **Balance:** the actual closing balance is outside the snapshot's `low`–`high`.
  - **Baseline categories** (the forecast for that month came from a `baseline` line): the difference is outside the category's own range, the 10th–90th percentile of the one-month errors (actual − baseline), replayed over the backtest origins like `_backtest_errors`, per category.
  - **Other categories:** `|difference|` above €5.
  - **Totals:** never flagged; they show only the difference.
- **Current month pace** (`pace`):
  - **Expected so far:**
    - booked lines and dated lines (recurring, planned, salary, consulting, transfers, reimbursements) count when their date is on or before today;
    - `baseline` lines count by the elapsed share of the month: day of month ÷ days in month.
  - **Actual so far:** the month's counted transactions up to today.
  - **`ahead`:** an expense category whose actual exceeds its expected amount by more than €5.
- **Hints:** each miss in a complete month gets at most one hint, in this order:
  1. `amount_changed`: the forecast came from a recurring item, and its actual amount in the month differs. Returns `recurring_id`.
  2. `one_off`: a single transaction in the category is at least half the difference and above `one_off_factor` × the forecast.
  3. `make_recurring`: the forecast was 0, and the category had a similar amount (within 10%) in at least 2 of the 3 previous months.
  4. `baseline_low` / `baseline_high`: the forecast came from a baseline, and the difference had the same sign and was at least 10% of the forecast in each of the last 3 complete months with a snapshot.
- **Summary:**
  - `months_tracked`: the number of complete months;
  - `typical_balance_miss`: the median of `|balance difference|` over those months, or null.

## API

`GET /api/forecast/vs-actual?account_type=personal|business`. Kids returns 400.

```json
{
  "account_type": "personal",
  "today": "2026-12-05",
  "months": [
    {
      "month": "2026-11",
      "snapshot_date": "2026-11-01",
      "status": "complete",
      "income":  {"forecast": "3200.00", "actual": "3200.00", "difference": "0.00"},
      "expense": {"forecast": "2950.00", "actual": "3120.50", "difference": "170.50"},
      "balance": {"forecast": "2000.00", "low": "1700.00", "high": "2400.00", "actual": "1850.00", "difference": "-150.00", "miss": false},
      "categories": [
        {"category_id": 12, "category": "Groceries", "flow": "expense", "forecast": "564.00", "actual": "612.00", "difference": "48.00", "miss": true, "source": "baseline"}
      ]
    }
  ],
  "pace": {
    "month": "2026-12", "through": "2026-12-05", "elapsed": 0.161,
    "income":  {"expected": "0.00", "actual": "0.00", "difference": "0.00"},
    "expense": {"expected": "640.00", "actual": "725.00", "difference": "85.00"},
    "categories": [
      {"category_id": 12, "category": "Groceries", "flow": "expense", "expected": "92.00", "actual": "130.00", "difference": "38.00", "ahead": true}
    ]
  },
  "summary": {"months_tracked": 1, "typical_balance_miss": "150.00"},
  "misses": [
    {"month": "2026-11", "category_id": 12, "category": "Groceries", "flow": "expense", "forecast": "564.00", "actual": "612.00", "difference": "48.00", "hint": "baseline_low", "recurring_id": null}
  ]
}
```

- **`months`:** every month with a snapshot, oldest first, up to and including the current month. The current month's `actual` values are so far, and its balance `actual` is the latest balance.
- **`categories`:** the union of the categories in the snapshot and in the actual transactions. `source` is the forecast's main source: the one with the largest share of its amount, or `none` when the forecast is 0.
- **`pace`:** null unless the current month has a snapshot.
- **`misses`:** complete months only, largest `|difference|` first, at most 20.
- **Money:** formatted like the rest of the API, with `format_money`.

## Where it shows

All three appear only when the Forecast switch (S) is on.

1. **Year table:** each month with a snapshot shows the difference from the forecast in small text under its figure. It applies to category rows, the income and expense totals, and Latest balance. For example, `+48`; the tooltip has the forecast, and differences under a euro are left out. The month in progress is compared with the pace, not the whole month, and its balance is left out. A miss is shown in the danger or success color: overspending or less income is danger. Other differences are muted. The tooltip lists forecast, actual, difference and the snapshot date. It must still fit at 1024px and 850px.
2. **Balance graph:** for each month with a snapshot, a marker for the predicted month-end balance next to the actual line. Hovering shows actual, predicted and range.
3. **Forecast page, new "Forecast vs reality" section** for the selected entity:
   - **Summary tiles:** typical month-end miss, the current month's pace (expense difference so far), and months tracked.
   - **Pace:** the categories running ahead this month.
   - **Biggest misses:** month, category, forecast, actual, difference, hint.
   - **Hints:** each links to where you'd act, and never changes anything itself:
     - `amount_changed` opens that recurring item in Recurring;
     - `make_recurring` opens Recurring;
     - `baseline_low` and `baseline_high` go to the Baselines section;
     - `one_off` has no link.
   - **Empty state:** explain that the first comparison appears after the first full month with a snapshot.

## Out of scope

Kids, comparing per forecast line, replaying the model for months before snapshots existed, and one-click fixes.
