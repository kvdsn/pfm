# Liquidation reserve

Model Business's liquidation reserve in the forecast: the reserves already formed, the share of each year's profit put into a new one, the tax on forming it, and the payouts that pay down the director loan. Belgian rules for small companies (art. 184quater WIB 92), all as settings to confirm with the accountant.

## The rules modelled

- **Forming:** part of a year's profit after corporate tax is booked as a reserve. Business pays a separate tax on it, 10% of the reserve. A reserve R therefore uses R × 1.1 of the after-tax profit.
- **Waiting:** a reserve formed on the accounts of year Y can be paid out at the low rate from 1 January of Y + wait + 1: 3 years for reserves formed from 2026 (5 years before).
- **Payout:** Business withholds 6.5% of the gross (5% for older reserves) and pays it to the tax office. The net is credited to the director's current account, so it pays down the director loan. No money moves between the bank accounts.
- **Not modelled:** payouts before the wait (20%), payout on winding up, and the general meeting's own date.

## Settings

- `liquidation_reserves`: the reserves already formed, `[{label, amount, payable, rate}]`: the gross amount, the date it can be paid out at the low rate, and the withholding rate in percent. Default empty.
- `reserve_share`: the percentage of each year's after-tax profit put into a new reserve, from the current year on. Default 0 (none).
- `reserve_tax_rate`: 10. `reserve_wait_years`: 3. `reserve_payout_rate`: 6.5.

## Forecast lines (Business)

- **Forming:** for each forecast year Y with `reserve_share` > 0: reserve R = share × (profit − corporate tax) ÷ (1 + tax rate), using the corporate tax estimate's profit and tax for Y. A line for the tax, R × tax rate, on 20 December of Y (with the last prepayment), source `reserve`, role category `reserve_tax` (default "Liquidation reserve tax"). Not when the year's profit after tax is zero or less.
- **Payout:** each reserve, existing or new, on its payable date (an existing one already past it: in the current month), if within the forecast. A line for the withholding, gross × rate, source `reserve`, role category `reserve_withholding` (default "Liquidation reserve withholding").
- **Profit:** both categories are left out of the corporate tax profit.
- **Missing categories:** without the role categories, no reserve lines are added and Business's assumptions say so.
- **Order:** after the later-year corporate tax and before the balance walk.

## Director loan

- Each payout adds a movement of −net (gross − withholding) on its date, marked as a reserve payout.
- The balance may go below zero: Business then owes the director, later transfers first use up that credit, and interest is never charged on a negative balance (as now).
- The yearly rows gain `repaid` (net payouts of the year).

## Money plan

Per year, two more figures:

- **Reserve:** the gross reserves formed and not yet paid out at year end.
- **Director loan:** the loan's balance at year end.

**Room, net** changes: the part of the room up to the reserve counts at the payout rate, the rest at the dividend withholding.

## Frontend

- **Money plan settings:** the reserves as editable rows (label, amount, payable date, rate), and the four settings.
- **Money plan table:** the two new columns.
- **Director loan section:** reserve payouts shown in the movements and the yearly rows.

## Tests

- Forming: the tax line from profit and share; none at share 0 or with a loss.
- Payout: the withholding line on the payable date; an existing reserve past its date pays out in the current month.
- Director loan: the net lowers the balance, which can go below zero without interest.
- Money plan: reserve and loan figures, and the split room net.
- Profit: the two categories are left out.
