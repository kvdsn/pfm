"""Forecast snapshots and the comparison of what they predicted with what happened.

The first forecast computed in a calendar month is saved for Personal and Business and never replaced.
Reality is always read live from the transactions, with the figures of the year table.
"""

import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Optional

from .database import ACCOUNT_TYPES, UNCATEGORIZED, connect, format_money
from .forecast import (
    TOLERANCE_CENTS,
    _balances,
    _band,
    _baseline_index,
    _category_baselines,
    _Context,
    _index,
    _load,
    _month_end,
    _month_key,
    _origins,
    _percentile,
    _project,
    _round_cents,
    _run_backtests,
)
from .recurring import _round_half_away
from .reports import _account_balances, report_flow_type

logger = logging.getLogger("pfm")

SNAPSHOT_ENTITIES = tuple(name for name in ACCOUNT_TYPES if name != "kids")
SMALL_CENTS = 500  # a difference up to this is not a miss where there is no range to judge it by
PACE_MIN_CENTS = 500
ONE_OFF_SHARE = 0.5
SIMILAR_SHARE = 0.1
BASELINE_HINT_SHARE = 0.1
MAKE_RECURRING_MONTHS = 3
BASELINE_HINT_MONTHS = 3
MAX_MISSES = 20
BANK_LOOKBACK_DAYS = 90

Key = tuple[int, str]  # (category id, flow)


def snapshot_exists(database_path: Path, today: Optional[date] = None) -> bool:
    month = _month_key(_index(today or date.today()))
    with connect(database_path) as db:
        return db.execute("SELECT 1 FROM forecast_snapshots WHERE month = ?", (month,)).fetchone() is not None


def ensure_snapshot(database_path: Path, today: Optional[date] = None) -> bool:
    """Save this month's snapshot unless there is one; True when one was taken."""
    today = today or date.today()
    if snapshot_exists(database_path, today):
        return False
    return take_snapshot(database_path, today)


def take_snapshot(database_path: Path, today: Optional[date] = None) -> bool:
    """Save the forecast of Personal and Business as it stands, once per calendar month.

    Per entity and month: the projected balance with its band, and the projected lines. For the snapshot
    month the transactions already booked are saved too, one line per category and day.
    """
    today = today or date.today()
    context = _load(database_path, today)
    lines, assumptions, indexes = _project(context)
    closings, _ = _balances(context, lines, assumptions)
    backtests = _run_backtests(context, indexes)
    month = _month_key(context.current)
    taken_at = datetime.combine(today, datetime.now().time()).isoformat(timespec="seconds")
    with connect(database_path) as db:
        inserted = db.execute(
            "INSERT OR IGNORE INTO forecast_snapshots(month, taken_at) VALUES (?, ?)", (month, taken_at)
        )
        if inserted.rowcount == 0:
            return False
        snapshot_id = inserted.lastrowid
        for entity in SNAPSHOT_ENTITIES:
            data = context.entities[entity]
            rows: list[tuple[Any, ...]] = []
            for number in context.months:
                key = _month_key(number)
                low, high = _band(backtests[entity]["errors"], number - context.current + 1)
                db.execute(
                    "INSERT INTO forecast_snapshot_balances VALUES (?, ?, ?, ?, ?, ?)",
                    (snapshot_id, entity, key, closings[entity][number], closings[entity][number] + low,
                     closings[entity][number] + high),
                )
                for line in lines[entity].get(number, []):
                    name, is_income = data.categories[line["category_id"]]
                    rows.append((
                        snapshot_id, entity, key, line["category_id"], name, int(is_income), line["amount"],
                        line["source"], line["date"].isoformat() if line["date"] else None, line["recurring_id"],
                    ))
            booked: dict[tuple[int, date], int] = defaultdict(int)
            for transaction in data.transactions:
                if _index(transaction["date"]) == context.current:
                    booked[transaction["category_id"], transaction["date"]] += transaction["cents"]
            for (category_id, day), cents in sorted(booked.items(), key=lambda item: (item[0][1], item[0][0])):
                name, is_income = data.categories[category_id]
                rows.append((snapshot_id, entity, month, category_id, name, int(is_income), cents, "booked",
                             day.isoformat(), None))
            db.executemany(
                """
                INSERT INTO forecast_snapshot_lines(snapshot_id, account_type, month, category_id, category_name,
                    is_income, amount_cents, source, day, recurring_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    return True


def ensure_snapshot_safely(database_path: Path, today: Optional[date] = None) -> None:
    """ensure_snapshot for the forecast request: a failure is logged and never reaches the caller."""
    try:
        ensure_snapshot(database_path, today)
    except Exception:
        logger.exception("Could not save the forecast snapshot")


# --- Comparison --------------------------------------------------------------------------

def _flow(categories: dict[int, tuple[str, bool]], category_id: int, cents: int) -> str:
    name, is_income = categories.get(category_id, (UNCATEGORIZED, False))
    return report_flow_type(is_income, name, cents)


def _shown(flow: str, cents: int) -> int:
    """A signed amount as the year table shows it: income as is, expense negated."""
    return cents if flow == "income" else -cents


def _output_id(categories: dict[int, tuple[str, bool]], category_id: int) -> Optional[int]:
    """The id the year table uses for the category's row: none for Uncategorized."""
    return None if categories.get(category_id, ("", False))[0] == UNCATEGORIZED else category_id


def _sum(values: dict[Key, int], flow: str) -> int:
    return sum(cents for (_, kind), cents in values.items() if kind == flow)


def _pair(forecast: int, actual: int) -> dict[str, str]:
    return {"forecast": format_money(forecast), "actual": format_money(actual),
            "difference": format_money(actual - forecast)}


def _status(db: Any, entity: str, month: str, current: str) -> str:
    """`current`, `complete` (every active bank account was imported after the month ended) or `waiting`."""
    if month == current:
        return "current"
    end = _month_end(_index(date.fromisoformat(f"{month}-01"))).isoformat()
    start = date.fromordinal(date.fromisoformat(end).toordinal() - BANK_LOOKBACK_DAYS).isoformat()
    rows = db.execute(
        """
        SELECT MAX(substr(i.imported_at, 1, 10)) AS last_import
        FROM accounts a LEFT JOIN imports i ON i.account_id = a.id
        WHERE a.account_type = ? AND a.kind = 'bank' AND EXISTS (
            SELECT 1 FROM transactions t
            WHERE t.account_id = a.id AND t.deleted_at IS NULL AND t.booking_date > ? AND t.booking_date <= ?
        )
        GROUP BY a.id
        """,
        (entity, start, end),
    ).fetchall()
    return "complete" if rows and all(row["last_import"] and row["last_import"] > end for row in rows) else "waiting"


def _balance_at(db: Any, entity: str, day: date) -> int:
    accounts = [a for a in _account_balances(db, entity) if a.dates[0] <= day.isoformat()]
    return sum(balance if (balance := a.at(day.isoformat())) is not None else a.opening for a in accounts)


def _baseline_errors(context: _Context, entity: str) -> dict[int, list[int]]:
    """Per category, the one-month errors (actual minus baseline) over the backtest origins, as `_backtest_errors`
    replays them at horizon 1."""
    settings = context.settings
    index = _baseline_index(context, entity)
    errors: dict[int, list[int]] = defaultdict(list)
    for origin in _origins(context, index):
        bases, _ = _category_baselines(
            index, origin, settings["baseline_months"], settings["baseline_weighted"], settings["one_off_factor"]
        )
        for category, by_month in index.by_category.items():
            base = bases.get(category)
            threshold = base.threshold if base else None
            actual = sum(t["cents"] for t in by_month.get(origin, ()) if threshold is None or abs(t["cents"]) <= threshold)
            errors[category].append(actual - (base.cents if base else 0))
    return errors


class _Month:
    """One snapshot month of one entity: forecast and actual figures by (category, flow)."""

    def __init__(self, month: str, snapshot_date: str, status: str) -> None:
        self.month, self.snapshot_date, self.status = month, snapshot_date, status
        self.forecast: dict[Key, int] = defaultdict(int)
        self.actual: dict[Key, int] = defaultdict(int)
        self.weights: dict[Key, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.recurring: dict[Key, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.balance: dict[str, int] = {}
        self.balance_actual = 0
        self.lines: list[Any] = []

    def source(self, key: Key) -> str:
        if not self.forecast.get(key):
            return "none"
        weights = {name: w for name, w in self.weights[key].items() if name != "booked"} or self.weights[key]
        return max(sorted(weights), key=lambda name: weights[name])


def forecast_vs_actual(database_path: Path, account_type: str, today: Optional[date] = None) -> dict[str, Any]:
    """Each month with a snapshot against what happened, the current month's pace, and the biggest misses with hints."""
    if account_type not in SNAPSHOT_ENTITIES:
        raise ValueError("Forecast vs reality covers Personal and Business")
    today = today or date.today()
    current = _month_key(_index(today))
    context = _load(database_path, today)
    data = context.entities[account_type]
    categories = data.categories

    with connect(database_path) as db:
        snapshots = db.execute(
            "SELECT id, month, taken_at FROM forecast_snapshots WHERE month <= ? ORDER BY month", (current,)
        ).fetchall()
        months: list[_Month] = []
        for snapshot in snapshots:
            month = _Month(snapshot["month"], snapshot["taken_at"][:10], _status(db, account_type, snapshot["month"], current))
            balance = db.execute(
                "SELECT closing_cents, low_cents, high_cents FROM forecast_snapshot_balances "
                "WHERE snapshot_id = ? AND account_type = ? AND month = ?",
                (snapshot["id"], account_type, month.month),
            ).fetchone()
            month.balance = dict(balance) if balance else {}
            month.lines = db.execute(
                "SELECT category_id, category_name, is_income, amount_cents, source, day, recurring_id "
                "FROM forecast_snapshot_lines WHERE snapshot_id = ? AND account_type = ? AND month = ? ORDER BY id",
                (snapshot["id"], account_type, month.month),
            ).fetchall()
            end = min(_month_end(_index(date.fromisoformat(f"{month.month}-01"))), today)
            month.balance_actual = _balance_at(db, account_type, end)
            months.append(month)

    stored_names: dict[int, str] = {}
    for month in months:
        for line in month.lines:
            stored_names.setdefault(line["category_id"], line["category_name"])
            known = line["category_id"] in categories
            name, is_income = categories[line["category_id"]] if known else (line["category_name"], bool(line["is_income"]))
            flow = report_flow_type(is_income, name, line["amount_cents"])
            key = (line["category_id"], flow)
            month.forecast[key] += _shown(flow, line["amount_cents"])
            month.weights[key][line["source"]] += abs(line["amount_cents"])
            if line["recurring_id"] is not None:
                month.recurring[key][line["recurring_id"]] += line["amount_cents"]

    by_month: dict[str, dict[Key, int]] = defaultdict(lambda: defaultdict(int))
    transactions_by_key: dict[tuple[str, Key], list[dict[str, Any]]] = defaultdict(list)
    for transaction in data.transactions:
        flow = _flow(categories, transaction["category_id"], transaction["cents"])
        key = (transaction["category_id"], flow)
        label = _month_key(_index(transaction["date"]))
        by_month[label][key] += _shown(flow, transaction["cents"])
        transactions_by_key[label, key].append(transaction)
    for month in months:
        month.actual = by_month[month.month]
    so_far: dict[Key, int] = defaultdict(int)
    if current in {m.month for m in months}:
        for transaction in data.transactions:
            if _month_key(_index(transaction["date"])) == current and transaction["date"] <= today:
                flow = _flow(categories, transaction["category_id"], transaction["cents"])
                so_far[transaction["category_id"], flow] += _shown(flow, transaction["cents"])

    def name_of(category_id: int) -> str:
        return categories[category_id][0] if category_id in categories else stored_names.get(category_id, UNCATEGORIZED)

    errors_cache: list[dict[int, list[int]]] = []

    def category_range(category_id: int, flow: str) -> Optional[tuple[int, int]]:
        if not errors_cache:
            errors_cache.append(_baseline_errors(context, account_type))
        errors = errors_cache[0].get(category_id)
        if not errors:
            return None
        shown = [_shown(flow, e) for e in errors]
        return _percentile(shown, 0.1), _percentile(shown, 0.9)

    def is_miss(month: _Month, key: Key, difference: int) -> bool:
        if month.status != "complete":
            return False
        if month.source(key) == "baseline" and (span := category_range(*key)) is not None:
            return difference < span[0] or difference > span[1]
        return abs(difference) > SMALL_CENTS

    rows_by_month: dict[str, dict[Key, dict[str, Any]]] = {}
    output_months = []
    for month in months:
        keys = sorted(
            {k for k, v in month.forecast.items() if v or month.actual.get(k)} | {k for k, v in month.actual.items() if v},
            key=lambda k: (-abs(month.actual.get(k, 0) - month.forecast.get(k, 0)), name_of(k[0]), k[1]),
        )
        rows: dict[Key, dict[str, Any]] = {}
        for key in keys:
            forecast, actual = month.forecast.get(key, 0), month.actual.get(key, 0)
            rows[key] = {
                "category_id": _output_id(categories, key[0]) if key[0] in categories else key[0],
                "category": name_of(key[0]), "flow": key[1], **_pair(forecast, actual),
                "miss": is_miss(month, key, actual - forecast), "source": month.source(key),
                "_forecast": forecast, "_difference": actual - forecast,
            }
        rows_by_month[month.month] = rows
        closing = month.balance.get("closing_cents", 0)
        low, high = month.balance.get("low_cents", closing), month.balance.get("high_cents", closing)
        output_months.append({
            "month": month.month, "snapshot_date": month.snapshot_date, "status": month.status,
            "income": _pair(_sum(month.forecast, "income"), _sum(month.actual, "income")),
            "expense": _pair(_sum(month.forecast, "expense"), _sum(month.actual, "expense")),
            "balance": {
                "forecast": format_money(closing), "low": format_money(low), "high": format_money(high),
                "actual": format_money(month.balance_actual), "difference": format_money(month.balance_actual - closing),
                "miss": month.status == "complete" and not low <= month.balance_actual <= high,
            },
            "categories": [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows.values()],
        })

    complete = [m for m in months if m.status == "complete"]
    hints = _Hints(context, account_type, database_path, complete, rows_by_month, by_month, transactions_by_key)
    misses = []
    for month in complete:
        for key, row in rows_by_month[month.month].items():
            if row["miss"]:
                hint, recurring_id = hints.hint(month, key, row)
                misses.append({
                    "month": month.month, "category_id": row["category_id"], "category": row["category"],
                    "flow": row["flow"], "forecast": row["forecast"], "actual": row["actual"],
                    "difference": row["difference"], "hint": hint, "recurring_id": recurring_id,
                    "_size": abs(row["_difference"]),
                })
    misses.sort(key=lambda miss: -miss["_size"])
    for miss in misses:
        del miss["_size"]

    balance_misses = [abs(m.balance_actual - m.balance.get("closing_cents", 0)) for m in complete]
    pace = None
    if current in {m.month for m in months}:
        pace = _pace(next(m for m in months if m.month == current), categories, name_of, today, so_far)
    return {
        "account_type": account_type, "today": today.isoformat(), "months": output_months, "pace": pace,
        "summary": {
            "months_tracked": len(complete),
            "typical_balance_miss": format_money(_round_half_away(median(balance_misses))) if balance_misses else None,
        },
        "misses": misses[:MAX_MISSES],
    }


def _pace(
    month: _Month, categories: dict[int, tuple[str, bool]], name_of: Any, today: date, so_far: dict[Key, int]
) -> dict[str, Any]:
    """Expected against actual so far in the current month: dated lines once their date has come, baselines pro rata."""
    days = _month_end(_index(today)).day
    elapsed = today.day / days
    expected: dict[Key, int] = defaultdict(int)
    for line in month.lines:
        name, is_income = categories[line["category_id"]] if line["category_id"] in categories else (line["category_name"], bool(line["is_income"]))
        flow = report_flow_type(is_income, name, line["amount_cents"])
        if line["source"] == "baseline":
            cents = _round_cents(Decimal(line["amount_cents"]) * today.day / days)
        elif line["day"] is not None and line["day"] <= today.isoformat():
            cents = line["amount_cents"]
        else:
            continue
        expected[line["category_id"], flow] += _shown(flow, cents)
    rows = []
    for key in {k for k, v in expected.items() if v} | {k for k, v in so_far.items() if v}:
        difference = so_far.get(key, 0) - expected.get(key, 0)
        rows.append({
            "category_id": _output_id(categories, key[0]) if key[0] in categories else key[0],
            "category": name_of(key[0]), "flow": key[1], "expected": format_money(expected.get(key, 0)),
            "actual": format_money(so_far.get(key, 0)), "difference": format_money(difference),
            "ahead": key[1] == "expense" and difference > PACE_MIN_CENTS, "_difference": difference,
        })
    rows.sort(key=lambda row: (-row["_difference"], row["category"]))
    for row in rows:
        del row["_difference"]
    return {
        "month": month.month, "through": today.isoformat(), "elapsed": round(elapsed, 3),
        **{flow: {"expected": format_money(_sum(expected, flow)), "actual": format_money(_sum(so_far, flow)),
                  "difference": format_money(_sum(so_far, flow) - _sum(expected, flow))}
           for flow in ("income", "expense")},
        "categories": rows,
    }


class _Hints:
    """Why a category missed, as at most one hint each; the transactions load only when a hint needs them."""

    def __init__(
        self, context: _Context, entity: str, database_path: Path, complete: list[_Month],
        rows: dict[str, dict[Key, dict[str, Any]]], actual_by_month: dict[str, dict[Key, int]],
        transactions: dict[tuple[str, Key], list[dict[str, Any]]],
    ) -> None:
        self.context, self.entity, self.database_path = context, entity, database_path
        self.complete, self.rows, self.actual_by_month, self.transactions = complete, rows, actual_by_month, transactions
        self.item_keys: Optional[dict[int, tuple[str, str]]] = None

    def hint(self, month: _Month, key: Key, row: dict[str, Any]) -> tuple[Optional[str], Optional[int]]:
        difference, forecast = row["_difference"], row["_forecast"]
        recurring_id = self._amount_changed(month, key)
        if recurring_id is not None:
            return "amount_changed", recurring_id
        # Without a forecast every payment would pass as a one-off, so a regular one is checked first.
        if forecast == 0 and self._make_recurring(month.month, key):
            return "make_recurring", None
        factor = self.context.settings["one_off_factor"]
        sign = 1 if difference > 0 else -1
        for transaction in self.transactions.get((month.month, key), []):
            shown = _shown(key[1], transaction["cents"])
            if shown * sign >= abs(difference) * ONE_OFF_SHARE and abs(shown) > factor * abs(forecast):
                return "one_off", None
        if month.source(key) == "baseline" and self._baseline_drift(month, key):
            return ("baseline_low" if difference > 0 else "baseline_high"), None
        return None, None

    def _amount_changed(self, month: _Month, key: Key) -> Optional[int]:
        planned = month.recurring.get(key)
        if not planned:
            return None
        if self.item_keys is None:
            with connect(self.database_path) as db:
                self.item_keys = {
                    r["id"]: (r["merchant_key"], r["kind"])
                    for r in db.execute("SELECT id, merchant_key, kind FROM recurring_items WHERE account_type = ?", (self.entity,))
                }
        found = []
        for item_id, expected in planned.items():
            item_key = self.item_keys.get(item_id)
            if item_key is None:
                continue
            paid = sum(t["cents"] for t in self.transactions.get((month.month, key), []) if t["key"] == item_key)
            if abs(paid - expected) > TOLERANCE_CENTS:
                found.append((-abs(paid - expected), item_id))
        return min(found)[1] if found else None

    def _make_recurring(self, month: str, key: Key) -> bool:
        actual = self.actual_by_month.get(month, {}).get(key, 0)
        index = _index(date.fromisoformat(f"{month}-01"))
        similar = sum(
            1 for back in range(1, MAKE_RECURRING_MONTHS + 1)
            if (before := self.actual_by_month.get(_month_key(index - back), {}).get(key, 0))
            and abs(before - actual) <= SIMILAR_SHARE * abs(actual)
        )
        return similar >= 2

    def _baseline_drift(self, month: _Month, key: Key) -> bool:
        recent = [m for m in self.complete if m.month <= month.month][-BASELINE_HINT_MONTHS:]
        if len(recent) < BASELINE_HINT_MONTHS:
            return False
        sign = 1 if self.rows[month.month][key]["_difference"] > 0 else -1
        for earlier in recent:
            row = self.rows[earlier.month].get(key)
            if row is None or not row["_forecast"]:
                return False
            if row["_difference"] * sign <= 0 or abs(row["_difference"]) < BASELINE_HINT_SHARE * abs(row["_forecast"]):
                return False
        return True
