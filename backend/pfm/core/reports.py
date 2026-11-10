"""Yearly reports and balance history."""

import calendar
import sqlite3
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .database import UNCATEGORIZED, connect, default_flow_type, format_money, masked_iban

# Income categories count their signed amounts as income. Every other category counts as expense
# with the amount negated, so a refund lowers it. Uncategorized goes by the sign of the amount.
REPORT_FLOW_SQL = (
    "CASE WHEN c.is_income = 1 THEN 'income' "
    f"WHEN c.id IS NULL OR c.name = '{UNCATEGORIZED}' "
    "THEN CASE WHEN t.amount_cents > 0 THEN 'income' ELSE 'expense' END "
    "ELSE 'expense' END"
)


# A transfer to or from another account of the same account type moves money without
# earning or spending it, so it is left out of income and expense totals.
OWN_TRANSFER_SQL = (
    "EXISTS (SELECT 1 FROM accounts own WHERE own.iban = t.counterparty_iban "
    "AND own.account_type = a.account_type AND own.id != a.id)"
)
# A bank settlement of a card statement whose purchases are imported is replaced by them.
COUNTED_SQL = f"t.replaced_by_statement_id IS NULL AND NOT {OWN_TRANSFER_SQL}"


def report_flow_type(is_income: bool, category: Optional[str], amount_cents: int) -> str:
    """The group a counted transaction belongs to; the Python twin of REPORT_FLOW_SQL."""
    if is_income:
        return "income"
    if category is None or category == UNCATEGORIZED:
        return default_flow_type(amount_cents)
    return "expense"


@dataclass
class _AccountBalances:
    """One account's post-transaction balance snapshots in chronological order."""

    iban: str
    dates: list[str] = field(default_factory=list)
    balances: list[int] = field(default_factory=list)
    opening: int = 0

    def at(self, day: str) -> Optional[int]:
        """Balance at the end of the given ISO day, if the account has history by then."""
        index = bisect_right(self.dates, day)
        return self.balances[index - 1] if index else None

    def before(self, day: str) -> int:
        """Balance at the start of the given ISO day; the opening balance before any history."""
        index = bisect_left(self.dates, day)
        return self.balances[index - 1] if index else self.opening


# The chain order of an account's rows with a balance. Within one bank export the file order is the bank's
# running-balance order: row_number 1 is the newest row, and from the highest row_number down to 1 each row's
# balance minus its amount equals the previous row's balance. A file is not sorted by date, though (a row dated
# the 20th can follow rows dated the 21st when it was booked late), so the order is taken from the file, not
# from the dates. `m` is the running maximum of balance_date in file order within the import: within one import
# it follows the file exactly, imports interleave by date, and a day shared by two imports goes to the later one.
# Use CHAIN_ROWS_SQL as the table and CHAIN_ORDER_SQL as the ORDER BY (reversed: the newest row is last).
CHAIN_ROWS_SQL = """(
    SELECT t.*, MAX(t.balance_date) OVER (
        PARTITION BY t.account_id, t.import_id ORDER BY t.row_number DESC ROWS UNBOUNDED PRECEDING
    ) AS m
    FROM transactions t WHERE t.deleted_at IS NULL AND t.balance_cents IS NOT NULL
)"""
CHAIN_ORDER_SQL = "m, import_id, row_number DESC"
CHAIN_ORDER_NEWEST_FIRST_SQL = "m DESC, import_id DESC, row_number ASC"


def _account_balances(db: sqlite3.Connection, account_type: str) -> list[_AccountBalances]:
    """Balances by booking date: the closing balance of a day is the opening balance plus everything booked up
    to that day. Each row's effective amount is its balance minus the previous row's in chain order, so a real
    gap in the data shows as a jump on the date of the first row after it."""
    rows = db.execute(
        f"""
        SELECT t.account_id, a.iban, t.balance_date, t.balance_cents, t.source_amount_cents
        FROM {CHAIN_ROWS_SQL} t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = ?
        ORDER BY t.account_id, {CHAIN_ORDER_SQL}
        """,
        (account_type,),
    ).fetchall()
    chains: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        chains.setdefault(row["account_id"], []).append(row)
    result: list[_AccountBalances] = []
    for chain in chains.values():
        first = chain[0]
        opening = first["balance_cents"] - first["source_amount_cents"]
        entries: list[tuple[str, int]] = []
        previous = opening
        for row in chain:
            entries.append((row["balance_date"], row["balance_cents"] - previous))
            previous = row["balance_cents"]
        entries.sort(key=lambda entry: entry[0])  # stable: chain order within a day
        account = _AccountBalances(iban=first["iban"], opening=opening)
        running = opening
        for day, amount in entries:
            running += amount
            account.dates.append(day)
            account.balances.append(running)
        result.append(account)
    return result


def _month_end(month: str) -> str:
    year, month_number = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(year, month_number)[1]:02d}"


def _same_day_last_year(day: str) -> str:
    """The same date a year earlier; 29 February becomes the 28th."""
    year, month, day_number = int(day[:4]) - 1, int(day[5:7]), int(day[8:10])
    return date(year, month, min(day_number, calendar.monthrange(year, month)[1])).isoformat()


def _month_summary(month: str, month_rows: list[sqlite3.Row], accounts: list[Any], month_end: str) -> dict[str, Any]:
    income_cents = 0
    expense_cents = 0
    own_transfer_cents = 0
    counted = 0
    category_totals: dict[tuple[str, Optional[int], str], list[int]] = {}
    for row in month_rows:
        if row["own_transfer"]:
            own_transfer_cents += max(0, -row["amount_cents"])
            continue
        counted += 1
        flow = report_flow_type(bool(row["is_income"]), row["category"], row["amount_cents"])
        if flow == "income":
            income_cents += row["amount_cents"]
        else:
            expense_cents -= row["amount_cents"]
        name = row["category"] or UNCATEGORIZED
        category_id = None if name == UNCATEGORIZED else row["category_id"]
        entry = category_totals.setdefault((flow, category_id, name), [0, 0])
        entry[0] += -row["amount_cents"] if flow == "expense" else row["amount_cents"]
        entry[1] += 1
    month_start = f"{month}-01"
    return {
        "month": month,
        "income": format_money(income_cents),
        "expenses": format_money(expense_cents),
        "net": format_money(income_cents - expense_cents),
        "own_transfers": format_money(own_transfer_cents),
        "categories": [
            {
                "flow_type": flow,
                "category_id": category_id,
                "name": name,
                "amount": format_money(amount),
                "transaction_count": count,
            }
            for (flow, category_id, name), (amount, count) in sorted(
                category_totals.items(), key=lambda item: (item[0][0], item[0][2])
            )
        ],
        # An account without transactions in a month carries its last balance forward.
        "account_balances": [
            {
                "account": masked_iban(account.iban),
                "opening": format_money(account.before(month_start)),
                "closing": format_money(account.at(month_end)),
            }
            for account in accounts
            if account.dates[0] <= month_end
        ],
        "transaction_count": counted,
    }


def report(database_path: Path, account_type: str, today: Optional[date] = None) -> dict[str, Any]:
    """Monthly income, expenses, category totals, and account balances, oldest month first.

    For a fair year-over-year comparison of the current year, `previous_year_to_date` holds last
    year's month of the latest transaction this year, cut off at the same day; it is None when
    there are no transactions this year.
    """
    with connect(database_path) as db:
        rows = db.execute(
            f"""
            SELECT substr(t.booking_date, 1, 7) AS month, t.booking_date, t.amount_cents, c.is_income,
                   t.category_id, c.name AS category, {OWN_TRANSFER_SQL} AS own_transfer
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE a.account_type = ? AND t.deleted_at IS NULL AND t.replaced_by_statement_id IS NULL
            ORDER BY month
            """,
            (account_type,),
        ).fetchall()
        accounts = _account_balances(db, account_type)

    rows_by_month: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        rows_by_month.setdefault(row["month"], []).append(row)
    summaries = [
        _month_summary(month, month_rows, accounts, _month_end(month)) for month, month_rows in rows_by_month.items()
    ]

    this_year = str((today or date.today()).year)
    latest = max((row["booking_date"] for row in rows if row["booking_date"].startswith(f"{this_year}-")), default=None)
    previous_year_to_date = None
    if latest is not None:
        through = _same_day_last_year(latest)
        month_rows = [row for row in rows_by_month.get(through[:7], []) if row["booking_date"] <= through]
        previous_year_to_date = {**_month_summary(through[:7], month_rows, accounts, through), "through": through}
    return {
        "account_type": account_type,
        "months": [summary["month"] for summary in summaries],
        "summaries": summaries,
        "previous_year_to_date": previous_year_to_date,
    }


def balance_history(
    database_path: Path,
    account_type: str,
    month: Optional[str],
    year: Optional[str],
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Total balance across accounts per day of a month, or per month of a year.

    Points are (label, last day) pairs; days and months after today are left out.
    """
    if month:
        first = date.fromisoformat(f"{month}-01")
        days = calendar.monthrange(first.year, first.month)[1]
        points = [(f"{month}-{day:02d}", f"{month}-{day:02d}") for day in range(1, days + 1)]
        starts = [label for label, _ in points]
    elif year:
        points = [
            (f"{year}-{month_number:02d}", _month_end(f"{year}-{month_number:02d}"))
            for month_number in range(1, 13)
        ]
        starts = [f"{label}-01" for label, _ in points]
    else:
        raise ValueError("Balance history requires a month or year")
    today_iso = (today or date.today()).isoformat()
    points = [point for point, start in zip(points, starts) if start <= today_iso]
    if not points:
        return {"points": []}
    period_end = points[-1][1]

    with connect(database_path) as db:
        accounts = [
            account for account in _account_balances(db, account_type)
            if account.dates[0] <= period_end
        ]

    def total_at(day: str) -> int:
        return sum(
            balance if (balance := account.at(day)) is not None else account.opening
            for account in accounts
        )

    return {"points": [{"date": label, "balance": format_money(total_at(end))} for label, end in points]}
