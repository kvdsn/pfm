# Personal Finance Manager

A local-only browser app for your bank exports. It imports KBC, Belfius and Revolut account exports and KBC and Belfius card statements, categorizes transactions with rules you control, and shows yearly reports and a forecast for three separate entities: Personal, Business and Kids.

Everything stays on your computer: the app listens on `127.0.0.1` only and makes no external requests.

## Setup

Requires Python 3.9+ and Node.js 18+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
PFM_BACKUP_DIR=/path/to/backup-folder PYTHONPATH=backend .venv/bin/python -m pfm
```

Then open `http://127.0.0.1:8000`.

To try the app without your own bank exports, build a demo database with made-up data and start the app on it:

```sh
PYTHONPATH=backend .venv/bin/python -m pfm.demo
PFM_HOME=data/demo PYTHONPATH=backend .venv/bin/python -m pfm
```

### Where your data lives

- **Database:** `data/finance.sqlite3`, created empty on first start. Set `PFM_HOME` to use another folder. Keep it out of cloud-synced folders: sync tools can corrupt SQLite's `-wal` and `-shm` files.
- **Backups:** in `PFM_BACKUP_DIR`, or next to the database when it isn't set. This folder can be synced.
- **Imported files:** a copy of each imported file in `source-data/<entity>/<account>/` in the backup folder.

The whole `data/` folder is ignored by git; keep the backup folder outside the repository.

## Importing

Open **Accounts** and choose **Import files**. You can select several files at once:

- **KBC** account CSVs and card CSVs
- **Belfius** account CSVs and card statement PDFs
- **Revolut** account statement CSVs (EUR only; pending, reverted and declined rows are skipped)

The preview shows each account's new and already-imported transactions. For an account it hasn't seen before, you choose once whether it is Personal, Business or Kids. Duplicates are always skipped, so overlapping exports are fine. Belfius exports have no running balance, so the app reconstructs it from the export's final balance or from the balances it already knows.

Each card becomes its own account with its purchases. When a statement's purchases add up to a settlement on a bank account, the settlement no longer counts in totals, so nothing is counted twice. Select the settlement to see the purchases.

The app makes a backup before every import. **Undo import** in an account's details removes that import's transactions.

## Using the app

The top bar switches between the entities and years, and opens the pages below. Press **K** to see every keyboard shortcut.

### Overview

- **Year table:** income and expenses per category and month, with totals, averages, shares, net change and the latest balance. When the previous year has data, a column compares with the same period last year. Select a category to filter the transactions. The graph icon next to "Latest balance" shows the balance history.
- **Transactions:** click a category, or press Enter, to change it. The most likely category is suggested, and you can turn it into a rule. The **Set by** column shows whether a rule or you set the category.
- **Search** matches text and amounts: `41,50`, `-41` (money out), `40..50` (a range) or `hema 41,50`.
- **Bulk categorizing:** tick transactions and choose **Set category**, for one-off merchants where a rule isn't worth it.
- **Notes and follow-ups:** add a note to any transaction, or flag it to follow up, such as a reimbursement to ask for. The **Follow-ups** filter lists the open ones from all years.

Transfers between two of your own accounts in the same entity are shown muted and left out of the totals.

### Categories and rules

- **Categories:** rename, merge, remove or add categories, mark which ones count as **income**, and set yearly **budgets**. With budgets, the year table gets a **Left** column, which turns amber when spending runs ahead of pace and red when over budget.
- **Rules:** a rule matches text in the merchant, description or memo. The first matching rule in priority order wins. Edit a rule in place and the **Impact** pane shows what would change. Saving never changes transactions; **Apply** does, and never overrides a category you set by hand. Tags flag rules that never win, overlap earlier rules, or are very broad.
- **Triage:** lists Uncategorized transactions grouped by merchant, largest first. Categorize a whole group, or make a rule from it.

The details are in [Categorization rules](docs/CATEGORIZATION_RULES.md).

### Forecast

The year table and balance graph continue to the end of next year with projected amounts in italics, and a range on the graph. The **Forecast** switch beside the title turns this on and off. Hover a projected cell to see where an amount comes from.

The **Forecast** page holds what the projection is based on:

- **Recurring payments:** subscriptions, loans and other payments found in your transactions. Confirm or ignore them, or add one by hand.
- **Planned items:** one-off future income or expenses.
- **Salary** and **Entity transfers:** what Business pays Personal, keeping Personal above a minimum balance.
- **Consulting:** income from days worked × day rate, the VAT on it, your days off, and a check of past payments.
- **Corporate tax:** an estimate with advised prepayments. It is an estimate, not tax advice.
- **Director loan** and **reimbursements** between Business and Personal.
- **Baselines:** each other category's usual month, from the median of recent months.
- **Forecast vs reality:** for Personal and Business, how far off the forecast was. It shows the typical month-end miss, whether this month's spending runs ahead of pace, and the biggest misses with a hint about what to adjust. The year table and graph show the same differences while the Forecast switch is on.

### Accounts and backups

**Accounts** lists every account and card per entity, with its transaction count, latest balance and last import. When the latest transaction is more than 35 days old, it shows a reminder that a new export is due.

The **Backups** section below it makes a backup on demand and restores any backup, first backing up the current data. The app also backs up before risky actions, such as undoing an import or merging categories, and keeps the latest 20 automatic backups. It never deletes the ones you make. To restore by hand, quit the app and copy a backup over `finance.sqlite3`.

## Development

For frontend work, run the API as above and `cd frontend && npm run dev` in a second terminal. Otherwise, rebuild with `npm run build` after frontend changes, because the backend serves `frontend/dist`.

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests
cd frontend && npx tsc --noEmit -p . && npm test
```

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md). More documentation:

- [Requirements](docs/REQUIREMENTS.md): how the app behaves, in more detail.
- [Categorization rules](docs/CATEGORIZATION_RULES.md)
- [Backlog](docs/BACKLOG.md)
- [AGENTS.md](AGENTS.md): notes for coding agents.
