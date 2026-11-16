# Money plan

Bring the money plan into the forecast: how much Business keeps as a buffer, which wishlist purchases are paid out of it as dividends, and how much room is left each year. Today this is worked out by hand from the forecast, which stops at the end of next year.

## Goals

- **Further ahead:** a forecast that runs several years, so purchases years out can be planned.
- **Buffer:** a year-end target for Business, in months of costs.
- **Purchases by dividend:** a wishlist purchase entered once, as the net euros Personal needs, with the gross cost to Business worked out.
- **Room:** per year, what Business holds above its target, gross and net.

## 1. Horizon

- **Setting:** `forecast_end_year`, default 0 (meaning next year, as now). Values below next year are refused; at most the current year + 9.
- **What runs to it:** the forecast, its year ends, the year tabs (forecast years, as now), the snapshots, and the director loan. The backtest band already widens past its longest horizon.
- **Later years** reuse what is in force: the latest salary schedule (`salary_for_year` already does this), the latest day rate, the current baselines, and recurring items until their end date.
- **Assumptions:** the forecast lists one line when it runs past next year: "From <year>, salary, day rate, days off and baselines repeat the latest values."
- **Corporate tax page** stays on the current and next year.

## 2. Corporate tax in later years

Corporate tax comes only from planned items. Past next year there are none, so Business would grow too fast.

- **Rule:** for each year after next year with no corporate tax planned items and no booked corporate tax, the forecast adds the estimated prepayments as projected lines (source `corporate_tax`, not stored), one per deadline, as the advice would.
- **Order:** the estimate needs the year's profit, which needs the other lines. So it runs after `_project` and before the balance walk, year by year, since a year's tax is no cost for that year's profit.
- **Dividends are not costs:** the dividend category is left out of profit, like the corporate tax category.
- **Invariant change:** "Corporate tax comes only from planned items" becomes "only from planned items, or, after next year, the estimate where there are none".

## 3. Purchases paid by dividend

- **Entering one:** in Personal's planned items, a "Paid by dividend" option next to the amount. You enter the purchase as a net expense (for example −€1,500, category of your choice).
- **What the app creates,** linked with the existing links:
  - **Personal:** the purchase as entered, and a dividend income of +€1,500 on the same date (role `dividend_personal`, default category "Dividends", income).
  - **Business:** a dividend of −€2,000 on the same date (role `dividend_business`, default category "Dividends", expense): net ÷ (1 − withholding).
- **Setting:** `dividend_withholding_rate`, default 25 (percent). The gross is computed when the item is saved and stored; changing the rate later doesn't change saved items. Editing the purchase's amount or date updates its two linked lines.
- **Storage:** a new column `planned_items.dividend_group` (through `ADDED_COLUMNS`, no backfill needed: existing items get NULL) ties the three lines together, so deleting one deletes all three. The existing one-to-one link stays between the Personal dividend and the Business dividend.
- **Transfers:** the dividend lands in Personal on the day of the purchase, so the Personal minimum-balance top-up doesn't react to it.

## 4. Buffer target

- **Setting:** `buffer_months`, a list of (year, months), each in force until the next. Default empty: no target. Example: 2026: 3, 2027: 4, 2028: 5.
- **A month of costs:** the average monthly Business outflow of the year's projected months, from recurring items, salary, Entity transfers and baselines. It leaves out VAT, corporate tax, dividends and planned items. For the current year, if fewer than 3 months are projected, next year's average is used.
- **Override:** `buffer_month_cost`, default 0 (derive it). A fixed amount replaces the derived one in every year.
- **The transfer buffer** (`business_buffer`, €2,000) stays as it is: it is the floor Entity transfers respect, not the target.

## 5. Room per year

A new section on the Forecast page, for Business: **Money plan**, one row per forecast year:

| Column | What it is |
|---|---|
| Year end | Business's projected balance at 31 December |
| A month of costs | derived or overridden |
| Target | buffer months × a month of costs |
| Room | year end − target; negative in red |
| Room, net | room × (1 − withholding rate) |
| New this year, net | the change in net room since the previous year end |
| Lowest | the year's lowest month-end and its month, in months of costs |
| Dividends | the year's planned dividends, net |

- **Room already counts** the dividends planned that year: it is what is left for more.
- **The buffer settings** sit in the section itself (months per year, the override, the withholding rate), each with its own Save, like the other sections.
- **Personal and Kids** don't show the section.

## API

- `GET /api/forecast` gains, in Business's slice, `money_plan: [{year, closing, month_cost, month_cost_derived, target_months, target, room, room_net, new_net, lowest, lowest_month, dividends_net}]`.
- `POST /api/planned/personal` takes `paid_by_dividend: bool`; the response includes the created lines.
- `ForecastSettingsUpdate` gains `forecast_end_year`, `buffer_months`, `buffer_month_cost` and `dividend_withholding_rate`.

## Tests

- **Horizon:** with `forecast_end_year` set three years out, months and year ends run to its December; salary and day rate repeat.
- **Corporate tax:** a later year gets four estimated prepayment lines; a year with a planned item gets none; dividends don't lower profit.
- **Dividends:** a net purchase creates three lines with the gross at 25%; editing and deleting keep them together.
- **Buffer and room:** derived month cost leaves out VAT, tax, dividends and planned items; the override wins; room and net room per year.
- **Demo:** `pfm.demo` sets a buffer and one dividend purchase, so the section isn't empty.

## Not now

- Ranges for purchases (low and high estimates).
- Investments outside the app.
- Personal-side buffers.
- Recurring dividend plans (a monthly amount out of Business).

## Order of work

1. Horizon setting and later-year corporate tax (backend, tests).
2. Dividend purchases (backend, then the Personal planned items form).
3. Buffer target and the Money plan section.
4. Docs: REQUIREMENTS (Forecast), README, BACKLOG item 12 removed, AGENTS code map and the corporate tax invariant.
