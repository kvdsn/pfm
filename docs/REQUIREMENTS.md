# Requirements

How the app behaves, in more detail than the README. [CATEGORIZATION_RULES.md](CATEGORIZATION_RULES.md) covers categories and rules.

## Principles

- **Local only.** The app binds to `127.0.0.1`, makes no external requests, and sends no data to online services. Categorization is deterministic: rules only, no AI or guessing.
- **Three separate entities.** Personal, Business and Kids each have their own accounts, categories, rules and reports. Only the forecast links them (salary, Entity transfers, the director loan, reimbursements and linked items).
- **Your data is safe.** The app makes a backup before risky changes, never overwrites a backup, and never deletes a backup you made.
- **One currency.** Amounts are in euro, written the Belgian way: `€ 1.234,56`. The year table rounds to whole euros, and to a compact `12k` in narrow windows.

## Import

- **Formats:**
  - **KBC:** account CSVs in Windows-1252 or UTF-8 with BOM. The signed `Bedrag` is the amount, and `Saldo` the balance.
  - **Belfius:** account CSVs. They have no per-row balance, so balances are reconstructed backwards from the export's final balance, or chained from an already imported newer export of the same account, or else forward from the balances already known for the rows before and in the export. The final balance is used only when it was taken by the export's end date.
  - **Revolut:** account statement CSVs. The account is keyed as "Revolut Current EUR", because the export has no IBAN. Only completed rows are imported, and the amount is Amount minus Fee.
  - **Cards:** KBC card CSVs and Belfius card statement PDFs.
  - **Currency:** EUR only. Other currencies are rejected.
- **Accounts** are recognized by their IBAN, or for cards by the card number (KBC) or the statement's customer reference (Belfius). The first import of an unknown account asks which entity it belongs to, and the app remembers the answer.
- **Preview:** the app previews every import and stages it until you confirm. You can import several files at once, and the preview lists them per account.
- **Duplicates** are skipped by fingerprint, so overlapping exports can be imported freely. The original row data is kept. Belfius adds a statement and transaction number once a transaction is on a statement; a row that differs from an imported one only by these numbers is still a duplicate.
- **Source copies:** after you confirm, a copy of each file is saved in `source-data/<entity>/<bank> account|card <last 4 digits>/` in the backup folder. The name is the statement's debit date for a card statement, and the booking-date range otherwise. The app never overwrites or duplicates a copy. A failed copy doesn't stop the import. A folder moves with its account when the account changes entity.
- **Undo import** removes that import's transactions but keeps account mappings and categories. When those transactions have notes or follow-ups, the confirmation warns that they will be lost.

## Reports

- **Income or expense.** Each category counts as either income or expense, set with its **Income** checkbox:
  - In an **income** category, amounts count with their sign, so a negative amount lowers income.
  - In an **expense** category, amounts count negated, so a refund lowers the expense. The total can be negative.
  - **Uncategorized** counts positive amounts as income and the rest as expense.

  So every category sits in exactly one group, and selecting its row lists exactly the transactions behind its total.
- **Transfers between your own accounts** in the same entity are recognized by the counterparty IBAN. They are left out of the totals, shown muted, and summed in a separate row. Transfers between entities count on both sides.
- **Card statements.** A statement's purchases replace the bank settlement that paid it. The settlement must be in the same entity, within a week of the debit date, and for exactly the amount due. For KBC, it must also name the card's last four digits. A statement whose refunds outweigh its purchases carries its credit over to the next one.
- **Year table:**
  - **Columns:** twelve months, total, monthly average and share of the group. Then net change and the latest balance: the sum of each account's last known balance.
  - **Compared with last year:** shown when the previous year has data, against the same period of last year. For the current year, that runs up to the day of the latest transaction.
  - **Current month:** highlighted, with future months dimmed.
- **Budgets.** A category other than Uncategorized can have a yearly budget. The year table then gets a **Left** column:
  - It is amber when the amount used exceeds the budget times the share of the year that has passed (months so far, including the current one, out of 12).
  - It is red when over budget.
  - An income budget is a target to reach.
- **Removed transactions** are soft-deleted. They leave the totals, but card statements still count them, so their settlements stay replaced.

## Transactions

- **Search** covers the merchant, description, memo, note and amount:
  - A number such as `41`, `41,50` or `1.250,00` matches that amount in either direction. A single separator followed by three digits is a thousands separator.
  - A sign such as `-41` limits it to money out or in.
  - A range such as `40..50` matches amounts in that range, inclusive.
  - Together with other words, as in `hema 41,50`, the amount narrows the text match.
- **Category edits** change only that transaction and mark it as set by hand. Bulk categorizing does the same for every ticked transaction.
- **Notes and follow-ups:** a note holds up to 500 characters, and a follow-up is open or done. They never change a category or a total, and rules never match them. The Follow-ups and Done filters list all years.

## Recurring payments

- **Grouping:** suggestions come from counted transactions grouped by merchant and kind. Transactions less than 8 days apart count as one payment.
- **Cadence:**

  | Cadence | Payments needed | Gaps between them |
  |---|---|---|
  | Monthly | at least 4 in the last 400 days | at least 75% within 25–35 or 55–70 days |
  | Quarterly | at least 3 in the last 400 days | 80–100 days |
  | Yearly | at least 2 | 330–400 days |
- **Amount:** amounts form runs within 10% (at least €2) of the run's median. A single payment between two runs at the same level is skipped as a one-off. A yearly payment needs one amount, unless it is a direct debit.
- **Stopped:** a payment whose latest occurrence is more than 1.5 periods old isn't suggested.
- **Stored decisions:** only your decisions are stored: confirmed or ignored payments, and their amount schedules. A confirmed payment can be linked to one in another entity, such as Kids top-ups paid from Personal.

## Forecast

The forecast runs from the current month to December of next year, for all entities at once. Each projected line names its source:

- **Recurring payments:** confirmed ones, following their cadence, amount schedule and end date.
- **Planned items:** one-off entries you add. They can be linked across entities.
- **Consulting income:** days worked × day rate × (1 + VAT rate).
  - **Days worked:** working days (Monday to Friday, minus Belgian public holidays) minus your days off. Days off can be half days, and can repeat every 1 to 4 weeks.
  - **Payment:** on the last working day of the month after the work.
  - **VAT:** the VAT in each payment is paid on the 15th of the following month.
- **Salary:** a yearly schedule (monthly amount, last month, optional minimum afterwards), booked as Director remuneration in Business. Personal receives its net share.
- **Entity transfers:** each month they top Personal up to its minimum balance (€2,000 by default), without taking Business below its buffer (€2,000). A month that falls short is flagged.
- **Reimbursements:** open claims of what Business owes Personal, repaid after 30 days by default.
- **Baselines:** every other category's median of its last 6 complete months, leaving out recurring payments and one-offs above 3× its typical month. You can mark a category as closed to give it no baseline.
- **Range:** a rolling backtest gives the balance a low and high band.

The Forecast page also estimates two things:

- **Corporate tax:**
  - **Rate:** 20% on the first €100,000 and 25% above, when the year's salary reaches €51,000; otherwise 25%.
  - **Prepayments:** for the deadlines of 10 April, 10 July, 10 October and 20 December, with the surcharge for prepaying too little.
  - **Advice:** advised prepayments, which can replace that year's future corporate tax planned items.
  - **Settings:** all of these rules are settings, to confirm with your accountant.
- **Director loan:** Entity transfers from the loan start count as a loan to the director. It carries simple daily interest (3.5% by default), never on a negative balance.

### Forecast vs reality

For Personal and Business, the forecast is compared with what happened.

- **Snapshots:** the first time the forecast is computed in a month, the app saves it, once per month. A month is judged against its own snapshot, as the forecast showed it then: what was already booked, plus what was still projected.
- **Reality:** the year table's figures, so a late import fills a month in afterwards. A past month counts as complete once every active bank account has an import made after the month ended.
- **Miss:** a balance outside the snapshot's range, a baseline category outside its own range of past errors, or any other category off by more than €5. Only complete months count as misses.
- **Current month:** compared with the forecast's pace. Dated items count once their date has passed, and baselines by the share of the month gone.
- **Hints:** Amount changed (a recurring payment), One-off, Make recurring?, and Baseline too low or too high (three months in a row). They link to where you'd change something, and never change anything themselves.
- **Where:**
  - in the year table, the difference under each figure, with the details in its tooltip;
  - in the balance graph, a marker for each predicted month-end balance;
  - on the Forecast page, a section with the summary, this month's pace and the biggest misses.

The Forecast switch (S) hides all projections from the year table, the graph and the year tabs, and the comparison with them.

## Backups

- **Where:** backups go to `PFM_BACKUP_DIR`, or next to the database. Each is a `finance-backup-<date>-<time>[-note].sqlite3` file.
- **Automatic backups** are made:
  - before every import (once per batch)
  - before upgrading the database layout
  - before undoing an import, merging or removing a category, or restoring a backup
  - before applying or deleting a rule, at most once per 30 minutes

  If the backup fails, the action doesn't happen. The app keeps the latest 20 automatic backups.
- **Restore** first backs up the current data. It then replaces the database and upgrades an older layout.

## Interface

- **Layout:** one page with a top bar for the entity, year and pages. The top bar is one row from 1680px wide.
- **Tables** fit without horizontal scrolling at 1024px wide.
- **Appearance:** follows the system's light or dark setting. All text is monospace, with tabular numbers.
- **Messages:** short toasts at the bottom of the page. Dialogs show their errors next to the form.
- **Keyboard shortcuts** are listed only in the Keyboard shortcuts dialog (K) and a single footer hint. Global shortcuts are ignored while typing or while a dialog is open.
