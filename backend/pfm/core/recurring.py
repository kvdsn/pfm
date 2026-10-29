"""Recurring payments: detected from an entity's transactions, kept only once confirmed or ignored."""

import calendar
import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median, median_low
from typing import Any, Optional, Union

from .database import ACCOUNT_TYPES, UNCATEGORIZED, connect, format_money, require_account_type
from .reports import COUNTED_SQL
from .rules import transaction_texts
from .transactions import _alike_key, parse_amount_cents

CADENCES = ("monthly", "quarterly", "yearly")
STATUSES = ("confirmed", "ignored")
PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
# A payment has stopped when its latest occurrence is older than 1.5 periods.
STOP_DAYS = {"monthly": 45, "quarterly": 135, "yearly": 545}
# A confirmed item is flagged stopped when its next date is this many days past.
STOPPED_GRACE_DAYS = {"monthly": 20, "quarterly": 45, "yearly": 60}
WINDOW_DAYS = 400
MERGE_DAYS = 8  # transactions fewer days apart than this are one occurrence, such as a loan paid in two parts
RECENT_DAYS = 183
MIN_SHARE = 0.75
# Allowed days between occurrences; 55-70 days counts as one missed month, and a yearly payment may move a few weeks.
INTERVALS = {"monthly": ((25, 35), (55, 70)), "quarterly": ((80, 100),), "yearly": ((330, 400),)}
MIN_OCCURRENCES = {"monthly": 4, "quarterly": 3, "yearly": 2}
MIN_LATEST_RUN = {"monthly": 3, "quarterly": 2}
MAX_RUNS = 3
DIRECT_DEBIT = "Direct debit"


def _round_half_away(value: Union[int, float]) -> int:
    return int(value + (0.5 if value > 0 else -0.5))


def _tolerance(cents: Union[int, float]) -> float:
    """How far an amount may stray from a typical one: 10%, but at least 2 euro."""
    return max(abs(cents) * 0.10, 200)


def _add_months(day: date, months: int, day_of_month: Optional[int] = None) -> date:
    """The date `months` later, on `day_of_month` (default: the same day), clamped to the month's end."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month or day.day, last))


def _parse_date(text: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(text))
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from error


def _signed_cents(amount: Union[str, int, float, None]) -> int:
    """Cents for a signed amount ("-454", "-1.250,50", 12.5); never zero."""
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        try:
            cents = int((Decimal(str(amount)) * 100).to_integral_value())
        except InvalidOperation as error:
            raise ValueError(f"Invalid amount: {amount}") from error
    else:
        text = "".join(str(amount or "").replace("−", "-").replace("€", "").split())
        sign = -1 if text.startswith("-") else 1
        parsed = parse_amount_cents(text.lstrip("+-"))
        if parsed is None:
            raise ValueError(f"Invalid amount: {amount}")
        cents = sign * parsed
    if cents == 0:
        raise ValueError("An amount cannot be zero")
    return cents


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Detection ---------------------------------------------------------------------------

def _load_groups(db: sqlite3.Connection, account_type: str) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[int, str], int]:
    """The entity's countable transactions by alike key (newest first), the category names, and Uncategorized's id."""
    categories = {row["id"]: row["name"] for row in db.execute(
        "SELECT id, name FROM categories WHERE account_type = ?", (account_type,)
    )}
    uncategorized = next((cid for cid, name in categories.items() if name == UNCATEGORIZED), 0)
    rows = db.execute(
        f"""
        SELECT t.id, t.booking_date, t.amount_cents, t.category_id, t.merchant, t.description,
               t.reference, t.source_json, a.bank
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = ? AND t.deleted_at IS NULL AND {COUNTED_SQL}
        ORDER BY t.booking_date DESC, t.id DESC
        """,
        (account_type,),
    ).fetchall()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        texts = transaction_texts(dict(row), row["bank"])
        if not texts["merchant"]:
            continue
        category_id = row["category_id"] if row["category_id"] != uncategorized else None
        groups.setdefault(_alike_key(texts), []).append({
            "date": date.fromisoformat(row["booking_date"]), "cents": int(row["amount_cents"]),
            "category_id": category_id, "merchant": texts["merchant"],
        })
    return groups, categories, uncategorized


def _occurrences(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transactions close together as one occurrence (dated by its first one, amounts added), oldest first."""
    occurrences: list[dict[str, Any]] = []
    for transaction in sorted(transactions, key=lambda t: t["date"]):
        last = occurrences[-1] if occurrences else None
        if last is not None and (transaction["date"] - last["last"]).days < MERGE_DAYS:
            last["cents"] += transaction["cents"]
            last["last"] = transaction["date"]
            last["categories"].append(transaction["category_id"])
        else:
            occurrences.append({
                "date": transaction["date"], "last": transaction["date"],
                "cents": transaction["cents"], "categories": [transaction["category_id"]],
            })
    return occurrences


def _fits_intervals(cadence: str, window: list[dict[str, Any]]) -> bool:
    gaps = [(b["date"] - a["date"]).days for a, b in zip(window, window[1:])]
    ranges = INTERVALS[cadence]
    good = sum(1 for gap in gaps if any(low <= gap <= high for low, high in ranges))
    return bool(gaps) and good >= MIN_SHARE * len(gaps)


def _runs(window: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split occurrences into runs of similar amounts; a run continues while an amount stays near its median.

    A single occurrence between two runs at the same level is a one-off (an extra invoice on the
    same mandate, say): it is left out and the runs around it join.
    """
    runs: list[list[dict[str, Any]]] = []
    for occurrence in window:
        if runs:
            typical = median(o["cents"] for o in runs[-1])
            if abs(occurrence["cents"] - typical) <= _tolerance(typical):
                runs[-1].append(occurrence)
                continue
        runs.append([occurrence])
    index = 1
    while index < len(runs) - 1:
        before, after = median(o["cents"] for o in runs[index - 1]), median(o["cents"] for o in runs[index + 1])
        if len(runs[index]) == 1 and abs(after - before) <= _tolerance(before):
            runs[index - 1:index + 2] = [runs[index - 1] + runs[index + 1]]
        else:
            index += 1
    return runs


def _detect(occurrences: list[dict[str, Any]], today: date, kind: str = "") -> Optional[dict[str, Any]]:
    """The cadence and amount runs of a group's occurrences, or None when it is no recurring payment.

    A yearly payment needs amounts within the tolerance of each other, so holiday shops and gifts
    that happen to repeat a year apart are not suggested; a direct debit is recurring by its
    mandate, so its yearly amount may change freely.
    """
    if not occurrences:
        return None
    recent = [o for o in occurrences if (today - o["date"]).days <= WINDOW_DAYS]
    for cadence in CADENCES:
        source = occurrences if cadence == "yearly" else recent
        runs = _runs(source)
        # One-offs left out of the runs count neither for the cadence nor for the sign.
        window = sorted((o for run in runs for o in run), key=lambda o: o["date"])
        if len(window) < MIN_OCCURRENCES[cadence] or not _fits_intervals(cadence, window):
            continue
        if len({o["cents"] > 0 for o in window}) != 1 or any(o["cents"] == 0 for o in window):
            continue
        if (today - window[-1]["date"]).days > STOP_DAYS[cadence]:
            continue
        if cadence == "yearly":
            if len(runs) > 1 and kind != DIRECT_DEBIT:
                continue
            # Yearly amounts are few, so every change counts as a new amount.
            runs = []
            for occurrence in window:
                if runs and runs[-1][-1]["cents"] == occurrence["cents"]:
                    runs[-1].append(occurrence)
                else:
                    runs.append([occurrence])
        else:
            big = sum(len(run) for run in runs if len(run) >= 2)
            # Measured against every occurrence, so a series that is half one-offs is no recurring payment.
            if len(runs[-1]) < MIN_LATEST_RUN[cadence] or len(runs) > MAX_RUNS or big < MIN_SHARE * len(source):
                continue
        return {"cadence": cadence, "window": window, "runs": runs}
    return None


def _schedule(runs: list[list[dict[str, Any]]]) -> list[tuple[str, int]]:
    """One amount entry per run: its first date and its median amount."""
    return [(run[0]["date"].isoformat(), _round_half_away(median(o["cents"] for o in run))) for run in runs]


def _latest_run_values(run: list[dict[str, Any]]) -> tuple[int, Optional[int]]:
    """The median day of month and the most used category of the latest run."""
    day = median_low(o["date"].day for o in run)
    counts: dict[int, int] = {}
    for occurrence in run:
        for category_id in occurrence["categories"]:
            if category_id is not None:
                counts[category_id] = counts.get(category_id, 0) + 1
    return day, max(counts, key=lambda cid: counts[cid], default=None)


# --- Items -------------------------------------------------------------------------------

def _current_cents(schedule: list[tuple[str, int]], today: date) -> int:
    """The amount in force today: the latest entry that has begun, else the first."""
    begun = [entry for entry in sorted(schedule) if entry[0] <= today.isoformat()]
    return (begun[-1] if begun else sorted(schedule)[0])[1]


def scheduled_cents(schedule: list[tuple[str, int]], day: date) -> int:
    begun = [entry for entry in sorted(schedule) if entry[0] <= day.isoformat()]
    return (begun[-1] if begun else sorted(schedule)[0])[1]


def _monthly_cents(cents: int, cadence: str) -> int:
    return _round_half_away(cents / PERIOD_MONTHS[cadence])


def _history(transactions: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Sums per calendar month for the 13 months ending with today's; null for a month without any."""
    sums: dict[str, int] = {}
    for transaction in transactions:
        period = transaction["date"].strftime("%Y-%m")
        sums[period] = sums.get(period, 0) + transaction["cents"]
    periods = [_add_months(today.replace(day=1), -back).strftime("%Y-%m") for back in range(12, -1, -1)]
    return [{"period": period, "amount": format_money(sums[period]) if period in sums else None} for period in periods]


def _next_date(
    cadence: str, last: Optional[date], day_of_month: Optional[int], start: date, end: Optional[date],
    today: Optional[date] = None,
) -> Optional[date]:
    """The due day closest to one period after the last payment.

    Measuring from the payment rather than its month keeps a payment that slipped across a month
    boundary (due on the 30th, paid on the 1st) from skipping a period. Without a payment yet, such
    as one added by hand, it is the first due day from the start date on that is not before today.
    """
    months = PERIOD_MONTHS[cadence]
    if last is None:
        following, periods = start, 0
        while today is not None and following < today:
            periods += 1
            following = _add_months(start.replace(day=1), periods * months, day_of_month or start.day)
    else:
        target = _add_months(last, months)
        following = min(
            (_add_months(last.replace(day=1), offset, day_of_month or last.day) for offset in range(months - 1, months + 2)),
            key=lambda day: abs((day - target).days),
        )
    return None if end is not None and following > end else following


def _item(
    *, item_id: Optional[int], status: str, merchant: str, kind: str, cadence: str,
    day_of_month: Optional[int], category_id: Optional[int], categories: dict[int, str],
    schedule: list[tuple[str, int]], start: date, end: Optional[date], note: str,
    transactions: list[dict[str, Any]], occurrences: list[dict[str, Any]], flags: list[str],
    linked: Optional[dict[str, Any]], today: date,
) -> dict[str, Any]:
    """The API shape of a confirmed, ignored or candidate payment, from its transactions and stored values."""
    cents = _current_cents(schedule, today)
    last = occurrences[-1]["last"] if occurrences else None
    following = _next_date(cadence, occurrences[-1]["date"] if occurrences else None, day_of_month, start, end, today)
    return {
        "id": item_id,
        "status": status,
        "merchant": merchant,
        "kind": kind,
        "cadence": cadence,
        "day_of_month": day_of_month,
        "category_id": category_id,
        "category": categories.get(category_id) if category_id is not None else None,
        "amount": format_money(cents),
        "monthly_amount": format_money(_monthly_cents(cents, cadence)),
        "amounts": [{"effective_date": effective, "amount": format_money(amount)} for effective, amount in sorted(schedule)],
        "start_date": start.isoformat(),
        "end_date": end.isoformat() if end else None,
        "last_date": last.isoformat() if last else None,
        "next_date": following.isoformat() if following else None,
        "history": _history(transactions, today),
        "occurrences": sum(1 for o in occurrences if (today - o["date"]).days <= WINDOW_DAYS),
        "flags": flags,
        "linked": linked,
        "note": note,
        "_cents": _monthly_cents(cents, cadence),
    }


def _candidate(key: tuple[str, str], transactions: list[dict[str, Any]], categories: dict[int, str], today: date) -> Optional[dict[str, Any]]:
    occurrences = _occurrences(transactions)
    found = _detect(occurrences, today, key[1])
    if found is None:
        return None
    runs = found["runs"]
    day, category_id = _latest_run_values(runs[-1])
    flags = []
    if (today - occurrences[0]["date"]).days <= RECENT_DAYS:
        flags.append("new")
    if len(runs) > 1 and (today - runs[-1][0]["date"]).days <= RECENT_DAYS:
        flags.append("price_change")
    item = _item(
        item_id=None, status="candidate", merchant=transactions[0]["merchant"], kind=key[1],
        cadence=found["cadence"], day_of_month=day, category_id=category_id, categories=categories,
        schedule=_schedule(runs), start=runs[0][0]["date"], end=None, note="",
        transactions=transactions, occurrences=found["window"], flags=flags, linked=None, today=today,
    )
    item["_detected"] = found
    return item


def _stored_item(
    row: sqlite3.Row, schedule: list[tuple[str, int]], groups: dict[tuple[str, str], list[dict[str, Any]]],
    categories: dict[int, str], linked: Optional[dict[str, Any]], today: date,
) -> dict[str, Any]:
    start = date.fromisoformat(row["start_date"])
    end = date.fromisoformat(row["end_date"]) if row["end_date"] else None
    transactions = [t for t in groups.get((row["merchant_key"], row["kind"]), []) if t["date"] >= start]
    occurrences = _occurrences(transactions)
    flags: list[str] = []
    item = _item(
        item_id=row["id"], status=row["status"], merchant=row["merchant"], kind=row["kind"],
        cadence=row["cadence"], day_of_month=row["day_of_month"], category_id=row["category_id"],
        categories=categories, schedule=schedule, start=start, end=end, note=row["note"],
        transactions=transactions, occurrences=occurrences, flags=flags, linked=linked, today=today,
    )
    if row["status"] == "confirmed":
        if occurrences:
            latest = occurrences[-1]
            expected = scheduled_cents(schedule, latest["date"])
            if abs(latest["cents"] - expected) > _tolerance(expected):
                flags.append("price_change")
        following = item["next_date"]
        if end is None and following is not None:
            overdue = (today - date.fromisoformat(following)).days
            if overdue > STOPPED_GRACE_DAYS[row["cadence"]]:
                flags.append("stopped")
    return item


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _stored_rows(db: sqlite3.Connection, account_type: str) -> tuple[list[sqlite3.Row], dict[int, list[tuple[str, int]]]]:
    rows = db.execute(
        "SELECT * FROM recurring_items WHERE account_type = ? ORDER BY id", (account_type,)
    ).fetchall()
    schedules: dict[int, list[tuple[str, int]]] = {}
    for amount in db.execute(
        """
        SELECT item_id, effective_date, amount_cents FROM recurring_amounts
        WHERE item_id IN (SELECT id FROM recurring_items WHERE account_type = ?)
        ORDER BY effective_date
        """,
        (account_type,),
    ):
        schedules.setdefault(amount["item_id"], []).append((amount["effective_date"], amount["amount_cents"]))
    return rows, schedules


def _build(db: sqlite3.Connection, account_type: str, today: date) -> dict[str, Any]:
    groups, categories, _ = _load_groups(db, account_type)
    rows, schedules = _stored_rows(db, account_type)
    partners = {row["id"]: row for row in db.execute(
        "SELECT id, account_type, merchant, kind FROM recurring_items"
    )}
    items, ignored = [], []
    for row in rows:
        partner = partners.get(row["linked_item_id"])
        linked = None if partner is None else {
            "id": partner["id"], "account_type": partner["account_type"],
            "merchant": partner["merchant"], "kind": partner["kind"],
        }
        item = _stored_item(row, schedules.get(row["id"], []), groups, categories, linked, today)
        (items if row["status"] == "confirmed" else ignored).append(item)
    stored = {(row["merchant_key"], row["kind"]) for row in rows}
    candidates = [
        candidate for key, transactions in groups.items() if key not in stored
        for candidate in [_candidate(key, transactions, categories, today)] if candidate is not None
    ]
    items.sort(key=lambda i: (-abs(i["_cents"]), i["merchant"].casefold()))
    candidates.sort(key=lambda i: (-abs(i["_cents"]), i["merchant"].casefold()))
    ignored.sort(key=lambda i: i["merchant"].casefold())
    active = [
        i for i in items
        if i["end_date"] is None or date.fromisoformat(i["end_date"]) >= today
    ]
    income = sum(i["_cents"] for i in active if i["_cents"] > 0)
    expense = sum(i["_cents"] for i in active if i["_cents"] < 0)
    return {
        "monthly": {"income": format_money(income), "expense": format_money(expense), "net": format_money(income + expense)},
        "items": items,
        "candidates": candidates,
        "ignored": ignored,
    }


def confirmed_items(
    db: sqlite3.Connection, account_type: str, today: date,
    groups: Optional[dict[tuple[str, str], list[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """The entity's confirmed items as listed, each with its amount `_schedule` and group `_key`.

    `groups` are transactions by alike key; they are loaded when not given.
    """
    categories = {row["id"]: row["name"] for row in db.execute(
        "SELECT id, name FROM categories WHERE account_type = ?", (account_type,)
    )}
    if groups is None:
        groups = _load_groups(db, account_type)[0]
    rows, schedules = _stored_rows(db, account_type)
    return [
        {**_stored_item(row, schedules.get(row["id"], []), groups, categories, None, today),
         "_schedule": schedules.get(row["id"], []), "_key": (row["merchant_key"], row["kind"])}
        for row in rows if row["status"] == "confirmed"
    ]


def list_recurring(database_path: Path, account_type: str, today: Optional[date] = None) -> dict[str, Any]:
    """The entity's confirmed items, suggested candidates and ignored items, with the monthly totals."""
    require_account_type(account_type)
    with connect(database_path) as db:
        result = _build(db, account_type, today or date.today())
    return {
        "monthly": result["monthly"],
        **{name: [_clean(i) for i in result[name]] for name in ("items", "candidates", "ignored")},
    }


def _find(db: sqlite3.Connection, account_type: str, item_id: int, today: date) -> dict[str, Any]:
    result = _build(db, account_type, today)
    for item in result["items"] + result["ignored"]:
        if item["id"] == item_id:
            return _clean(item)
    raise LookupError("Recurring item not found")


def _stored_row(db: sqlite3.Connection, account_type: str, item_id: int) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM recurring_items WHERE id = ? AND account_type = ?", (item_id, account_type)
    ).fetchone()
    if row is None:
        raise LookupError("Recurring item not found")
    return row


def _check_category(db: sqlite3.Connection, account_type: str, category_id: Optional[int]) -> None:
    if category_id is None:
        return
    found = db.execute("SELECT account_type FROM categories WHERE id = ?", (category_id,)).fetchone()
    if found is None or found["account_type"] != account_type:
        raise ValueError("Category does not belong to this account type")


def _check_day(day_of_month: Optional[int]) -> None:
    if day_of_month is not None and not 1 <= day_of_month <= 31:
        raise ValueError("Day of month must be between 1 and 31")


def _check_cadence(cadence: str) -> None:
    if cadence not in CADENCES:
        raise ValueError("Cadence must be monthly, quarterly, or yearly")


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError("Status must be confirmed or ignored")


# --- Decisions ---------------------------------------------------------------------------

def confirm_recurring(
    database_path: Path, account_type: str, merchant: str, kind: str, status: str, *,
    cadence: Optional[str] = None, category_id: Optional[int] = None,
    amount: Union[str, int, float, None] = None, day_of_month: Optional[int] = None,
    end_date: Optional[str] = None, note: Optional[str] = None, today: Optional[date] = None,
) -> dict[str, Any]:
    """Confirm or ignore a detected candidate, storing its detected values with the overrides applied."""
    require_account_type(account_type)
    _check_status(status)
    if cadence is not None:
        _check_cadence(cadence)
    _check_day(day_of_month)
    today = today or date.today()
    key = (merchant.casefold(), kind)
    with connect(database_path) as db:
        if db.execute(
            "SELECT 1 FROM recurring_items WHERE account_type = ? AND merchant_key = ? AND kind = ?",
            (account_type, key[0], kind),
        ).fetchone():
            raise ValueError("That payment is already confirmed or ignored")
        groups, categories, _ = _load_groups(db, account_type)
        candidate = _candidate(key, groups.get(key, []), categories, today) if key in groups else None
        if candidate is None:
            raise LookupError("No recurring payment found for that merchant and kind")
        _check_category(db, account_type, category_id)
        end = _parse_date(end_date, "End date") if end_date else None
        schedule = _schedule(candidate["_detected"]["runs"])
        if amount not in (None, ""):
            schedule[-1] = (schedule[-1][0], _signed_cents(amount))
        start = date.fromisoformat(candidate["start_date"])
        if end is not None and end < start:
            raise ValueError("End date cannot be before the start date")
        stamp = _now()
        cursor = db.execute(
            """
            INSERT INTO recurring_items(account_type, merchant, merchant_key, kind, status, cadence,
                day_of_month, category_id, start_date, end_date, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_type, candidate["merchant"], key[0], kind, status, cadence or candidate["cadence"],
                day_of_month if day_of_month is not None else candidate["day_of_month"],
                category_id if category_id is not None else candidate["category_id"],
                start.isoformat(), end.isoformat() if end else None, note or "", stamp, stamp,
            ),
        )
        db.executemany(
            "INSERT INTO recurring_amounts(item_id, effective_date, amount_cents) VALUES (?, ?, ?)",
            [(cursor.lastrowid, effective, cents) for effective, cents in schedule],
        )
        return _find(db, account_type, int(cursor.lastrowid), today)


def add_recurring(
    database_path: Path, account_type: str, merchant: str, cadence: str, amount: Union[str, int, float], start_date: str, *,
    day_of_month: Optional[int] = None, end_date: Optional[str] = None, category_id: Optional[int] = None,
    note: Optional[str] = None, kind: str = "", today: Optional[date] = None,
) -> dict[str, Any]:
    """Add a confirmed recurring payment by hand, for one detection doesn't suggest.

    It follows the transactions of its merchant and `kind`, like a confirmed suggestion: they set its next
    date and history and leave the baselines. With a kind, that group must exist (`recurring_groups`);
    without one, it follows the merchant's transactions that have no kind, if any.
    """
    require_account_type(account_type)
    _check_cadence(cadence)
    _check_day(day_of_month)
    name = " ".join(merchant.split())
    if not name:
        raise ValueError("Enter a name for the payment")
    cents = _signed_cents(amount)
    start = _parse_date(start_date, "Start date")
    end = _parse_date(end_date, "End date") if end_date else None
    if end is not None and end < start:
        raise ValueError("End date cannot be before the start date")
    today = today or date.today()
    with connect(database_path) as db:
        if db.execute(
            "SELECT 1 FROM recurring_items WHERE account_type = ? AND merchant_key = ? AND kind = ?",
            (account_type, name.casefold(), kind),
        ).fetchone():
            raise ValueError(f"A recurring payment for {' · '.join(filter(None, (name, kind)))} already exists")
        if kind:
            groups, _, _ = _load_groups(db, account_type)
            if (name.casefold(), kind) not in groups:
                raise LookupError(f"No transactions of {name} · {kind}")
        _check_category(db, account_type, category_id)
        stamp = _now()
        cursor = db.execute(
            """
            INSERT INTO recurring_items(account_type, merchant, merchant_key, kind, status, cadence,
                day_of_month, category_id, start_date, end_date, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_type, name, name.casefold(), kind, cadence, day_of_month if day_of_month is not None else start.day,
                category_id, start.isoformat(), end.isoformat() if end else None, note or "", stamp, stamp,
            ),
        )
        db.execute(
            "INSERT INTO recurring_amounts(item_id, effective_date, amount_cents) VALUES (?, ?, ?)",
            (cursor.lastrowid, start.isoformat(), cents),
        )
        return _find(db, account_type, int(cursor.lastrowid), today)


def recurring_groups(database_path: Path, account_type: str, today: Optional[date] = None) -> list[dict[str, Any]]:
    """The entity's transaction groups (merchant and kind) with a payment in the last 400 days and no recurring
    payment yet, most recent first: what a payment added by hand can follow."""
    require_account_type(account_type)
    today = today or date.today()
    with connect(database_path) as db:
        groups, categories, _ = _load_groups(db, account_type)
        stored = {(row["merchant_key"], row["kind"]) for row in db.execute(
            "SELECT merchant_key, kind FROM recurring_items WHERE account_type = ?", (account_type,)
        )}
    result = []
    for key, transactions in groups.items():
        latest = transactions[0]  # newest first
        if key in stored or (today - latest["date"]).days > WINDOW_DAYS:
            continue
        category_id = next((t["category_id"] for t in transactions if t["category_id"] is not None), None)
        result.append({
            "merchant": latest["merchant"], "kind": key[1], "count": len(transactions),
            "first_date": transactions[-1]["date"].isoformat(), "last_date": latest["date"].isoformat(),
            "last_amount": format_money(latest["cents"]), "category_id": category_id,
            "category": categories.get(category_id) if category_id is not None else None,
        })
    result.sort(key=lambda group: (group["last_date"], group["merchant"].casefold()), reverse=True)
    return result


def update_recurring(
    database_path: Path, account_type: str, item_id: int, changes: dict[str, Any], today: Optional[date] = None
) -> dict[str, Any]:
    """Change a stored item. `changes` holds only the fields to set (end_date and category_id may be None).

    `amounts`, a list of effective_date and amount, replaces the whole schedule. Ignoring an item
    drops its link.
    """
    require_account_type(account_type)
    today = today or date.today()
    with connect(database_path) as db:
        row = _stored_row(db, account_type, item_id)
        values: dict[str, Any] = {}
        if "status" in changes:
            _check_status(changes["status"])
            values["status"] = changes["status"]
        if "cadence" in changes:
            _check_cadence(changes["cadence"])
            values["cadence"] = changes["cadence"]
        if "day_of_month" in changes:
            _check_day(changes["day_of_month"])
            values["day_of_month"] = changes["day_of_month"]
        if "category_id" in changes:
            _check_category(db, account_type, changes["category_id"])
            values["category_id"] = changes["category_id"]
        if "start_date" in changes:
            values["start_date"] = _parse_date(changes["start_date"], "Start date").isoformat()
        if "end_date" in changes:
            values["end_date"] = _parse_date(changes["end_date"], "End date").isoformat() if changes["end_date"] else None
        if "note" in changes:
            values["note"] = changes["note"] or ""
        start = values.get("start_date", row["start_date"])
        end = values.get("end_date", row["end_date"])
        if end is not None and end < start:
            raise ValueError("End date cannot be before the start date")
        schedule: Optional[list[tuple[str, int]]] = None
        if changes.get("amounts") is not None:
            schedule = [
                (_parse_date(entry["effective_date"], "Effective date").isoformat(), _signed_cents(entry["amount"]))
                for entry in changes["amounts"]
            ]
            if not schedule:
                raise ValueError("The amount schedule needs at least one entry")
            if len({effective for effective, _ in schedule}) != len(schedule):
                raise ValueError("Amount dates must be different")
        values["updated_at"] = _now()
        db.execute(
            f"UPDATE recurring_items SET {', '.join(f'{name} = ?' for name in values)} WHERE id = ?",
            (*values.values(), item_id),
        )
        if schedule is not None:
            db.execute("DELETE FROM recurring_amounts WHERE item_id = ?", (item_id,))
            db.executemany(
                "INSERT INTO recurring_amounts(item_id, effective_date, amount_cents) VALUES (?, ?, ?)",
                [(item_id, effective, cents) for effective, cents in schedule],
            )
        if values.get("status", "confirmed") != "confirmed":
            _unlink(db, [item_id])
        return _find(db, account_type, item_id, today)


def _unlink(db: sqlite3.Connection, item_ids: list[int]) -> None:
    """Clear the links of these items and of the items linked to them."""
    marks = ", ".join("?" for _ in item_ids)
    db.execute(
        f"UPDATE recurring_items SET linked_item_id = NULL WHERE id IN ({marks}) OR linked_item_id IN ({marks})",
        (*item_ids, *item_ids),
    )


def delete_recurring(database_path: Path, account_type: str, item_id: int) -> None:
    """Remove a decision so the payment can come back as a candidate; the counterpart's link is cleared."""
    require_account_type(account_type)
    with connect(database_path) as db:
        _stored_row(db, account_type, item_id)
        _unlink(db, [item_id])
        db.execute("DELETE FROM recurring_items WHERE id = ?", (item_id,))


def link_recurring(
    database_path: Path, account_type: str, item_id: int, linked_item_id: Optional[int], today: Optional[date] = None
) -> dict[str, Any]:
    """Link a confirmed item to a confirmed item of another entity, both ways; None unlinks.

    Any earlier link of either item is cleared first.
    """
    require_account_type(account_type)
    today = today or date.today()
    with connect(database_path) as db:
        row = _stored_row(db, account_type, item_id)
        if linked_item_id is None:
            _unlink(db, [item_id])
            return _find(db, account_type, item_id, today)
        other = db.execute("SELECT * FROM recurring_items WHERE id = ?", (linked_item_id,)).fetchone()
        if other is None:
            raise LookupError("Recurring item to link not found")
        if other["account_type"] == account_type:
            raise ValueError("Link an item of another account type")
        if row["status"] != "confirmed" or other["status"] != "confirmed":
            raise ValueError("Only confirmed items can be linked")
        _unlink(db, [item_id, linked_item_id])
        db.execute("UPDATE recurring_items SET linked_item_id = ? WHERE id = ?", (linked_item_id, item_id))
        db.execute("UPDATE recurring_items SET linked_item_id = ? WHERE id = ?", (item_id, linked_item_id))
        return _find(db, account_type, item_id, today)


def linkable_recurring(database_path: Path, account_type: str, today: Optional[date] = None) -> list[dict[str, Any]]:
    """The confirmed items of the other entities, to link an item to."""
    require_account_type(account_type)
    today = today or date.today()
    result = []
    with connect(database_path) as db:
        for other in (name for name in ACCOUNT_TYPES if name != account_type):
            for item in _build(db, other, today)["items"]:
                result.append({
                    "id": item["id"], "account_type": other, "merchant": item["merchant"], "kind": item["kind"],
                    "amount": item["amount"], "cadence": item["cadence"],
                })
    return result

