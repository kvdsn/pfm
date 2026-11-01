# Forecast vs reality

Compare what the forecast predicted for a month with what actually happened, for Personal and Business. Kids is left out.

## Goals

- **Trust:** how far off the forecast usually is.
- **Causes:** which categories made the difference.
- **This month:** whether spending runs ahead of the forecast while the month is still going.
- **Tuning:** hints for settings that look wrong, with a link to where you change them.

## Snapshots

The forecast is computed live and never stored, so the comparison needs saved snapshots. There is no history before the feature ships.

- **When:** automatically, the first time the forecast is computed in a new calendar month. One snapshot per month, never replaced.
- **Judged against:** each month is compared with the snapshot taken in that month. If the first run in October is on 20 October, that is October's snapshot.
- **Full month:** a snapshot stores whole-month projections for the snapshot month, ignoring what is already booked. Today the current month only counts lines after the as-of date (`_counted_net`), so the snapshot needs its own pass.
- **Contents:** per entity and month, from the snapshot month to the end of the forecast:
  - per category: the projected amount, and whether it is income;
  - the projected month-end balance, with its low and high range.

  Only the snapshot month is compared now. The later months are kept so a later version can show how a forecast held up over time.
- **Storage:** new tables from `SCHEMA` (`forecast_snapshots`, `forecast_snapshot_lines`). Store category ids and names, so renamed, merged or removed categories still show. Snapshots aren't changed by later imports, setting changes or restores of other data.
- **Reality:** always computed live from transactions, so a late import (October's data on 10 November) fills in October afterwards.

## Comparisons

- **Levels:** month-end balance, income and expense totals, and each category. These match the year table's rows. Own transfers are left out, as in the report.
- **Meaningful miss:** a difference outside the forecast's existing low–high range. Smaller differences are shown in the year table but aren't listed as misses.
- **Current month pace:** spending so far against a pro-rated forecast:
  - baseline categories scale by the share of the month elapsed;
  - recurring payments, planned items, salary and consulting count only up to their own dates.
- **Incomplete months:** a past month only counts as compared once its data is imported, meaning the latest import for each account covers the month's end. Until then it is marked as waiting for an import.

## Where it shows

All three appear only when the Forecast switch (S) is on.

1. **Year table:** each past month with a snapshot shows the difference from the forecast under its figure, for categories, totals and Latest balance. For example, `+48 vs 564`. It must still fit at 1024px and 850px.
2. **Balance graph:** a dashed marker for each month's predicted month-end balance next to the actual line. Hovering shows the actual and predicted values.
3. **Forecast page, new "Forecast vs reality" section:**
   - **Summary:** typical month-end miss, the current month's pace, and the number of months tracked.
   - **Biggest misses:** category, month, forecast, actual, difference, hint.
   - **Hints:** they suggest and link, and never change anything themselves:
     - a large one-off: "One-off";
     - a category with no forecast that has a regular amount: "Make recurring?", linking to Recurring;
     - a baseline category too high or too low three months in a row: "Baseline low/high", linking to the category's settings;
     - a recurring payment whose amount changed: "Amount changed", linking to the recurring item.

## Out of scope

- Kids.
- Comparing per forecast line (did each recurring payment or planned item arrive, when, and for how much).
- Replaying the model for months before snapshots existed.
- One-click fixes.

## Open points for the build

- Check that taking the snapshot on the first forecast request of the month doesn't slow the first page load noticeably.
- Decide whether a restore of the database should keep snapshots taken after the backup. Snapshots live in the database, so a restore rolls them back too. That is probably fine; confirm with the owner.
- Docs: REQUIREMENTS (Forecast), README overview, AGENTS code map (new module, likely `core/forecast_history.py`).
