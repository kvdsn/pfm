# Agent Instructions

A local-only personal finance manager: it imports bank and card exports, categorizes transactions with priority-ordered rules, and reports and forecasts Personal, Business and Kids finances by year. Backend: FastAPI and SQLite (`backend/pfm`). Frontend: React, TypeScript and Vite (`frontend/src`), built to `frontend/dist` and served by the backend. There is a single user, the owner, who reviews the work. User-facing behavior is in README.md, docs/REQUIREMENTS.md and docs/CATEGORIZATION_RULES.md; read those before changing behavior.

## Run, build, test

- **Database:** `data/finance.sqlite3`. The whole `data/` folder is git-ignored, and it also holds the imported files in `data/source-data/`.
  - Leave `PFM_HOME` unset.
  - `PFM_BACKUP_DIR` is the user's synced backup folder. Read it from the running server (`ps eww -o command= -p <pid>`) or ask, and never write it into the repo.
  - An empty app usually means `PFM_HOME` is set or the database is missing: the app silently creates an empty one.
- **Start the app** with absolute paths, because a relative `.venv/bin/python` fails from `frontend/`:
  `cd <repo> && env -u PFM_HOME PFM_BACKUP_DIR=<backup folder> PYTHONPATH=<repo>/backend <repo>/.venv/bin/python -m pfm`
  It serves `http://127.0.0.1:8000`. On start, `initialize` migrates the schema, after a "before upgrade" backup when needed.
- **Before handing back:**
  - **After backend changes:** restart the user's server on port 8000, or the rebuilt frontend talks to an old API.
  - **After frontend changes:** run `cd frontend && npm run build`.
- **Checks:**
  - **Backend:** `.venv/bin/python -m unittest discover -s tests`. Tests clear `PFM_BACKUP_DIR`/`PFM_HOME` themselves; keep it that way.
  - **Frontend:** `cd frontend && npx tsc --noEmit -p . && npm test` (vitest, jsdom).
  - **Layout:** `cd frontend && npm run layout-check`.
- **Data-changing tests** (restore, apply, undo, merge, import) never run on the live database:
  1. Copy it with SQLite's backup API to the scratchpad.
  2. Run a second server on port 8001 with `PFM_HOME`/`PFM_BACKUP_DIR` pointing at the copy: `python -c "import uvicorn; uvicorn.run('pfm.server:create_app', factory=True, port=8001)"`.
  3. Delete the copy afterwards.

## Code map

**Backend** (`backend/pfm`):
- **`server.py`:** every `/api/*` endpoint in `create_app`.
  - `safety_backup()` runs before risky endpoints.
  - `LookupError` maps to 404 and `ValueError` to 400.
- **`core/database.py`:** schema, `connect()` (one connection per call), `initialize()` with migrations, and backups (create, list, prune, restore).
- **Importing and accounts:**
  - `core/parsing.py`: KBC and Belfius CSVs, and duplicate fingerprints.
  - `core/revolut.py`: Revolut, keyed as `REVOLUT-CURRENT-EUR`.
  - `core/card_exports.py`: KBC card CSVs and Belfius PDFs.
  - `core/imports.py`: staging, committing and undoing imports, accounts, and the accounts overview.
  - `core/source_files.py`: copies of imported files. A failed copy is never fatal.
- **`core/statements.py`:** card statements and `refresh_card_settlements`. `statement_rows` replays its carried-credit logic for display; keep the two in sync.
- **Transactions and categories:**
  - `core/describe.py`: raw bank texts to a merchant and one detail line.
  - `core/rules.py`: matching, impact preview, and save, apply and delete.
  - `core/transactions.py`: listing and search, category edits, suggestions, and triage groups. Groupings share `_alike_key` (merchant ignoring case, plus kind).
  - `core/categories.py`: list, add, rename, merge and remove. Merge and remove keep budgets, recurring items and forecast settings in step.
  - `core/annotations.py`: notes and follow-ups.
  - `core/budgets.py`: yearly category budgets.
- **`core/reports.py`:** the yearly report, balance history, and own-transfer detection (`OWN_TRANSFER_SQL`, `COUNTED_SQL`).
- **Forecasting:**
  - `core/recurring.py`: recurring payment detection and the stored decisions.
  - `core/forecast.py`: the forecast for all entities at once, the backtest and the consulting check.
  - `core/forecast_settings.py`: settings (defaults resolved at read time), salary, day rates, days off, and the Belgian calendar.
  - `core/planned.py`, `core/corporate_tax.py`, `core/director_loan.py` and `core/reimbursements.py`: the remaining models.

**Frontend** (`frontend/src`):
- **`App.tsx`:** global state, the page switch, the forecast and the Forecast switch (`pfm.showForecast` in localStorage), and every global keyboard shortcut.
  - **Which part keys act on:** the Forecast and Accounts pages each have several parts, and `section` remembers the one last clicked, so N and the arrow keys act there.
  - **Always-mounted file input:** browsers open a file chooser only during a user gesture, so I and N use this input.
- **Overview:**
  - `TopBar.tsx`: the top bar and footer.
  - `YearOverview.tsx`: the year table. `forecast.ts` adds the projections and `budgets.ts` the pace rule.
  - `TransactionsPanel.tsx`: the transactions table, its sticky header and details dock, and statement rows. Sorting is in `transactionSort.ts`.
  - `CategoryCellEditor.tsx`: the inline category autocomplete.
- **Full pages:**
  - `TriageView.tsx`
  - `PlanView.tsx`: the Forecast page, with `RecurringView.tsx` inside it.
  - `RulesView.tsx`
  - `CategoriesView.tsx`
  - `AccountsView.tsx`: with `Imports.tsx`, and `Backups.tsx` below it.
- **Shared:**
  - `ShortcutsDialog.tsx`: `SHORTCUT_GROUPS`, the only list of shortcuts.
  - `hooks.ts`, `Dialog.tsx`, `Toast.tsx`, `BalanceChart.tsx`, `api.ts` and `types.ts`.
  - `styles.css`: color tokens on `:root`, redefined for dark mode. Use tokens, never hard-coded colors.

**Tests:** `tests/test_*.py` for the backend. Frontend tests are `*.test.tsx` next to their component.

## Invariants

- **Entities:** Personal, Business and Kids (`ACCOUNT_TYPES`) are fully separate, and each has a protected `Uncategorized`.
  - **Adding an entity:** extend `ACCOUNT_TYPES`, the `AccountType` Literal in `server.py`, and `ACCOUNT_TYPES` in `types.ts`.
  - **Stale CHECKs:** the `account_type` and `match_mode` CHECKs are derived from code. SQLite can't alter a CHECK, so `initialize` rebuilds stale tables (`_rebuild_stale_tables`).
- **Rules:**
  - **Match modes:** `contains`, `word` and `exact`. The frontend mirrors word matching in `wordPattern` (`ruleFields.ts`).
  - **Priority:** the first match in priority order wins.
  - **Who set it:** `category_source` (`rule`, `manual`, `default`) and `category_rule_id` record who set a category.
  - **Saving** changes nothing. **Applying** sets what the rule decides, except `manual`, and releases what it no longer decides. **Deleting** moves only its own transactions to Uncategorized.
- **Income and expense** come from the category's `is_income`, never from a `flow_type`. `REPORT_FLOW_SQL` and `report_flow_type` must agree, so a year-table row lists exactly the transactions in its total.
- **Settings:** every key in `forecast_settings.DEFAULTS` must also be a field of `ForecastSettingsUpdate`, or the endpoint drops it silently. A test checks this.
- **Forecast:**
  - Role categories and closed categories never get a baseline.
  - Corporate tax comes only from planned items.
  - Recurring candidates are never stored. Only confirmed or ignored decisions are.
- **New tables** (budgets, forecast, recurring, reimbursements) come from `SCHEMA` with `CREATE TABLE IF NOT EXISTS`, so they need no migration backup. New columns go through `ADDED_COLUMNS` with a backfill.
- **Card statements** replace the settlement that paid them (`replaced_by_statement_id`).
- **Soft delete:** removed transactions get `deleted_at`. Statement totals still count them.
- **Notes and follow-ups** never change a category or a total. Undo import hard-deletes them, hence its warning.
- **Backups:**
  - Automatic backups are recognized by their note (`AUTOMATIC_BACKUP_LABELS`). The latest 20 are kept, and manual ones are never deleted.
  - Undo import, category merge or remove, and restore always back up first. Rule apply or delete backs up at most once per 30 minutes.

## Pitfalls already hit

- **Backup files:** open them read-only with `?mode=ro&immutable=1`, and build the URI with `Path.as_uri()`. `mode=ro` alone fails on WAL files, and the path may contain `@`.
- **Migrations:** try a migration on a copy of the real database first, and report what the backfill did.
- **jsdom and key handlers:** jsdom lacks `scrollIntoView`, so call it as `?.()`. Global key handlers must ignore editable targets and open dialogs (`[role="dialog"][aria-modal="true"]`).
- **CSS specificity:** table-wide rules have beaten row-specific ones before.
- **`layout-check`:**
  - It drives Vivaldi headlessly and sometimes fails on an extension error; re-run it once.
  - A duplicated strip at the edges of its screenshots is a capture quirk.
  - It doesn't cover the transactions table, so check that table in the browser pane.

## Working with the user

- **Orchestrate by default:**
  - Do small, contained changes yourself.
  - Delegate substantial features to Sonnet sub-agents, and searches or doc syncs to Haiku, with complete briefs. Review their diffs and commit yourself.
  - Keep design decisions, data-safety calls and the final review for yourself.
- **Be efficient:**
  - Verify UI through DOM queries or small zoomed regions; take full screenshots only when the look itself must be judged.
  - Run targeted tests for small changes, and the full suites plus the layout check before committing a feature.
  - Keep briefs and reports concise.
  - The user starts a new session per topic, so rely on this file.
- **Committing:** when finished work passes its checks, commit to `main` and push without asking. Ask first for destructive git operations, such as a force push or rewriting history.

## Rules

- **Data safety:**
  - Never clear or overwrite the finance database or imported records without explicit confirmation.
  - Never commit database files, bank exports or backups.
- **The repository is public:**
  - Never put real names, IBANs, card or reference numbers, places, merchants or amounts from the user's data into code, tests, docs or commit messages. Use made-up values.
  - Never commit local paths or the backup folder.
- **Categorization:** keep the entities' data and rules separate. Ask before adding automatic classifications for ambiguous transactions. Never reapply rules without explicit confirmation.
- **Privacy:** keep the app local-only, with no external services.
- **Docs:** after behavior changes, update the user docs.
  - **README.md:** a short overview.
  - **docs/REQUIREMENTS.md:** behavior rules and defaults.
  - **docs/CATEGORIZATION_RULES.md:** categorization behavior.
  - **docs/BACKLOG.md:** ideas, in priority order. Remove an item once it is built.

  Keep them short and about what users see. Feature plans go in `docs/plans/`. Keep this file's code map and invariants current.
- **Shortcuts:** show keyboard shortcuts only in the Keyboard shortcuts dialog (K) and its single footer hint. Never add them to buttons, labels or tooltips.
- **Layout:**
  - **Widths:** optimize for 1728px, the owner's width. The top bar is one row from 1680px, and full-page views should use the width. Tables must still fit at 1024px and 850px without horizontal scrolling.
  - **Checks:** after layout or styling changes, run `npm run layout-check`, review its screenshots in `frontend/layout-screenshots/`, and check the affected views in the browser pane. The user's screenshot review is part of done.
