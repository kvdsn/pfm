"""Forecast inputs: settings, salary schedules, day rates and days off, and the Belgian working calendar."""

import calendar
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Union

from .database import ACCOUNT_TYPES, connect, format_money
from .recurring import _parse_date, _signed_cents
from .transactions import parse_amount_cents

DEFAULTS: dict[str, Any] = {
    "personal_min_balance": 200000,
    "business_buffer": 200000,
    # Each payment so far arrived at the end of the month after the month of work.
    "payment_delay_months": 1,
    "vat_rate": 21,
    "vat_delay_months": 1,
    "vat_day": 15,
    "baseline_months": 6,
    "baseline_weighted": False,
    "one_off_factor": 3,
    # The share of the salary Personal receives after withholding tax and contributions; 0 derives it from history.
    "salary_net_percent": 0,
    # Days after a reimbursement claim arises that Business is expected to repay it.
    "reimbursement_delay_days": 30,
    # Corporate tax rules (to confirm with the accountant); money in cents, rates in percent.
    "tax_reduced_threshold": 5100000,
    "tax_reduced_rate": 20,
    "tax_reduced_band": 10000000,
    "tax_standard_rate": 25,
    "tax_base_rate": 3,
    "tax_surcharge_applies": True,
    "tax_profit_excluded": [],
    # The director loan: Entity transfers to Personal from this date on, with simple interest at this rate.
    "loan_start": "2026-01-01",
    "loan_interest_rate": 3.5,
}
MONEY_SETTINGS = ("personal_min_balance", "business_buffer", "tax_reduced_threshold", "tax_reduced_band")
PERCENT_SETTINGS = {
    "tax_reduced_rate": ("Reduced rate", 0, 100),
    "tax_standard_rate": ("Standard rate", 0, 100),
    "tax_base_rate": ("Base rate", 0, 20),
    "loan_interest_rate": ("Loan interest rate", 0, 20),
}
WHOLE_RANGES = {
    "payment_delay_months": (0, 6),
    "vat_delay_months": (0, 6),
    "vat_day": (1, 28),
    "baseline_months": (3, 12),
    "reimbursement_delay_days": (0, 180),
}
# Each model's role: the entity it books in and the default category name there.
ROLES = {
    "consulting_income": ("business", "Consulting income"),
    "vat": ("business", "VAT"),
    "corporate_tax": ("business", "Corporate tax"),
    "salary_business": ("business", "Director remuneration"),
    "salary_personal": ("personal", "Salary"),
    "transfer_business": ("business", "Entity transfer"),
    "transfer_personal": ("personal", "Entity transfer"),
    "loan_interest_business": ("business", "Director loan interest"),
    "loan_interest_personal": ("personal", "Director loan interest"),
}
DEFAULT_CLOSED: dict[str, list[str]] = {"personal": [], "business": [], "kids": []}


# --- Settings ----------------------------------------------------------------------------

def _nonnegative_cents(value: Union[str, int, float, None], label: str) -> int:
    """Cents for a Belgian or plain amount that may be zero."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid {label}")
    text = re.sub(r"[\s€]", "", str(value))
    cents = parse_amount_cents(text)
    if cents is None:
        raise ValueError(f"Invalid {label}: {value}")
    return cents


def _whole(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a whole number")
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a whole number") from error
    if number != int(number) or not low <= number <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}")
    return int(number)


def _number(value: Any, label: str, low: float, high: Optional[float] = None) -> Union[int, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a number") from error
    if number < low or (high is not None and number > high):
        raise ValueError(f"{label} must be " + (f"from {low:g} to {high:g}" if high is not None else f"at least {low:g}"))
    return int(number) if number == int(number) else number


def _stored(db: sqlite3.Connection) -> dict[str, Any]:
    return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM forecast_settings")}


def _save(db: sqlite3.Connection, key: str, value: Any) -> None:
    db.execute(
        "INSERT INTO forecast_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def _category_id_by_name(db: sqlite3.Connection, account_type: str, name: str) -> Optional[int]:
    row = db.execute("SELECT id FROM categories WHERE account_type = ? AND name = ?", (account_type, name)).fetchone()
    return int(row["id"]) if row else None


def load_settings(db: sqlite3.Connection) -> dict[str, Any]:
    """Every setting with defaults filled in; money in cents, `roles` and `closed_categories` as category ids."""
    stored = _stored(db)
    settings = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    settings["tax_profit_excluded"] = list(settings["tax_profit_excluded"])
    saved_roles = stored.get("roles", {})
    settings["roles"] = {
        role: saved_roles[role] if role in saved_roles else _category_id_by_name(db, entity, name)
        for role, (entity, name) in ROLES.items()
    }
    saved_closed = stored.get("closed_categories")
    if saved_closed is None:
        saved_closed = {
            entity: [cid for name in names if (cid := _category_id_by_name(db, entity, name)) is not None]
            for entity, names in DEFAULT_CLOSED.items()
        }
    settings["closed_categories"] = {entity: list(saved_closed.get(entity, [])) for entity in ACCOUNT_TYPES}
    return settings


def get_settings(database_path: Path) -> dict[str, Any]:
    """The settings for the API: amounts formatted, roles as {role: category_id | null}."""
    with connect(database_path) as db:
        settings = load_settings(db)
    return {**settings, **{key: format_money(settings[key]) for key in MONEY_SETTINGS}}


def _check_category(db: sqlite3.Connection, account_type: str, category_id: Any, label: str) -> int:
    row = None if isinstance(category_id, bool) or not isinstance(category_id, int) else db.execute(
        "SELECT account_type FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None or row["account_type"] != account_type:
        raise ValueError(f"{label} must be a {account_type} category")
    return category_id


def _validated_changes(db: sqlite3.Connection, changes: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """The changes checked and in storage form; roles and closed categories merge over the current ones."""
    valid: dict[str, Any] = {}
    for key, value in changes.items():
        if key in MONEY_SETTINGS:
            valid[key] = _nonnegative_cents(value, key.replace("_", " "))
        elif key in WHOLE_RANGES:
            valid[key] = _whole(value, key.replace("_", " ").capitalize(), *WHOLE_RANGES[key])
        elif key == "vat_rate":
            valid[key] = _number(value, "VAT rate", 0, 100)
        elif key in PERCENT_SETTINGS:
            label, low, high = PERCENT_SETTINGS[key]
            valid[key] = _number(value, label, low, high)
        elif key == "tax_surcharge_applies":
            if not isinstance(value, bool):
                raise ValueError("Surcharge applies must be true or false")
            valid[key] = value
        elif key == "tax_profit_excluded":
            if not isinstance(value, list):
                raise ValueError("Excluded categories must be a list of category ids")
            valid[key] = sorted({_check_category(db, "business", cid, "An excluded category") for cid in value})
        elif key == "loan_start":
            valid[key] = _parse_date(value, "Loan start").isoformat()
        elif key == "salary_net_percent":
            valid[key] = _number(value, "Salary net percent", 0, 100)
        elif key == "one_off_factor":
            valid[key] = _number(value, "One-off factor", 1)
        elif key == "baseline_weighted":
            if not isinstance(value, bool):
                raise ValueError("Baseline weighted must be true or false")
            valid[key] = value
        elif key == "roles":
            if not isinstance(value, dict) or any(role not in ROLES for role in value):
                raise ValueError("Roles must name known roles: " + ", ".join(ROLES))
            roles = dict(current["roles"])
            for role, category_id in value.items():
                roles[role] = None if category_id is None else _check_category(db, ROLES[role][0], category_id, role)
            valid[key] = roles
        elif key == "closed_categories":
            if not isinstance(value, dict) or any(entity not in ACCOUNT_TYPES for entity in value):
                raise ValueError("Closed categories must be listed per account type")
            closed = {entity: list(ids) for entity, ids in current["closed_categories"].items()}
            for entity, ids in value.items():
                if not isinstance(ids, list):
                    raise ValueError("Closed categories must be lists of category ids")
                closed[entity] = sorted({_check_category(db, entity, cid, "A closed category") for cid in ids})
            valid[key] = closed
        else:
            raise ValueError(f"Unknown setting: {key}")
    return valid


def set_settings(database_path: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Validate and store any subset of the settings; returns all of them."""
    with connect(database_path) as db:
        for key, value in _validated_changes(db, changes, load_settings(db)).items():
            _save(db, key, value)
    return get_settings(database_path)


def remap_settings_category(db: sqlite3.Connection, source_id: int, target_id: Optional[int]) -> None:
    """Follow a merged (target given) or removed (target None) category in the roles and closed lists.

    A role that rested on a default is stored explicitly when merged, because the default name stops resolving.
    """
    settings = load_settings(db)
    if source_id in settings["roles"].values():
        roles = _stored(db).get("roles", {})
        for role, category_id in settings["roles"].items():
            if category_id == source_id and target_id is None:
                roles.pop(role, None)
            elif category_id == source_id:
                roles[role] = target_id
        _save(db, "roles", roles)
    closed = settings["closed_categories"]
    if any(source_id in ids for ids in closed.values()):
        _save(db, "closed_categories", {
            entity: list(dict.fromkeys(target_id if cid == source_id else cid for cid in ids if cid != source_id or target_id))
            for entity, ids in closed.items()
        })
    if source_id in settings["tax_profit_excluded"]:
        _save(db, "tax_profit_excluded", list(dict.fromkeys(
            target_id if cid == source_id else cid for cid in settings["tax_profit_excluded"] if cid != source_id or target_id
        )))


# --- Salary schedules --------------------------------------------------------------------

def _valid_year(year: str) -> str:
    if len(year) != 4 or not year.isdigit():
        raise ValueError("Year must use YYYY format")
    return year


def _salary_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "year": row["year"], "monthly_amount": format_money(row["monthly_cents"]), "last_month": row["last_month"],
        "minimum_amount": format_money(row["minimum_cents"]),
    }


def list_salary(database_path: Path) -> list[dict[str, Any]]:
    with connect(database_path) as db:
        return [_salary_view(row) for row in db.execute("SELECT * FROM salary_schedules ORDER BY year")]


def set_salary(
    database_path: Path, year: str, monthly_amount: Union[str, int, float], last_month: int,
    minimum_amount: Union[str, int, float, None] = None,
) -> dict[str, Any]:
    """Set or replace a year's salary: the monthly amount, paid up to and including `last_month`, and the
    minimum paid each month after it (empty or 0 for none)."""
    _valid_year(year)
    cents = _signed_cents(monthly_amount)
    if cents < 0:
        raise ValueError("The monthly salary must be positive")
    if isinstance(last_month, bool) or not isinstance(last_month, int) or not 1 <= last_month <= 12:
        raise ValueError("Last month must be between 1 and 12")
    minimum = _nonnegative_cents(minimum_amount, "The minimum salary") if minimum_amount not in (None, "") else 0
    with connect(database_path) as db:
        db.execute(
            """
            INSERT INTO salary_schedules(year, monthly_cents, last_month, minimum_cents) VALUES (?, ?, ?, ?)
            ON CONFLICT(year) DO UPDATE SET monthly_cents = excluded.monthly_cents, last_month = excluded.last_month,
                minimum_cents = excluded.minimum_cents
            """,
            (year, cents, last_month, minimum),
        )
        return _salary_view(db.execute("SELECT * FROM salary_schedules WHERE year = ?", (year,)).fetchone())


def delete_salary(database_path: Path, year: str) -> None:
    _valid_year(year)
    with connect(database_path) as db:
        if db.execute("DELETE FROM salary_schedules WHERE year = ?", (year,)).rowcount == 0:
            raise LookupError("Salary schedule not found")


def load_salary(db: sqlite3.Connection) -> dict[int, tuple[int, int, int]]:
    """{year: (monthly cents, last month, minimum cents after it)}."""
    return {
        int(row["year"]): (row["monthly_cents"], row["last_month"], row["minimum_cents"])
        for row in db.execute("SELECT * FROM salary_schedules")
    }


def salary_for_year(schedules: dict[int, tuple[int, int, int]], year: int) -> Optional[tuple[int, int, int, int]]:
    """(schedule year, monthly cents, last month, minimum cents) in force for a year: its own, else the latest earlier one."""
    source = year if year in schedules else max((y for y in schedules if y < year), default=None)
    return None if source is None else (source, *schedules[source])


# --- Day rates ---------------------------------------------------------------------------

def _rate_view(row: sqlite3.Row) -> dict[str, Any]:
    return {"effective_date": row["effective_date"], "rate": format_money(row["rate_cents"])}


def list_day_rates(database_path: Path) -> list[dict[str, Any]]:
    with connect(database_path) as db:
        return [_rate_view(row) for row in db.execute("SELECT * FROM day_rates ORDER BY effective_date")]


def set_day_rate(database_path: Path, effective_date: str, rate: Union[str, int, float]) -> dict[str, Any]:
    day = _parse_date(effective_date, "Effective date").isoformat()
    cents = _signed_cents(rate)
    if cents < 0:
        raise ValueError("The day rate must be positive")
    with connect(database_path) as db:
        db.execute(
            "INSERT INTO day_rates(effective_date, rate_cents) VALUES (?, ?) "
            "ON CONFLICT(effective_date) DO UPDATE SET rate_cents = excluded.rate_cents",
            (day, cents),
        )
        return _rate_view(db.execute("SELECT * FROM day_rates WHERE effective_date = ?", (day,)).fetchone())


def delete_day_rate(database_path: Path, effective_date: str) -> None:
    day = _parse_date(effective_date, "Effective date").isoformat()
    with connect(database_path) as db:
        if db.execute("DELETE FROM day_rates WHERE effective_date = ?", (day,)).rowcount == 0:
            raise LookupError("Day rate not found")


def load_day_rates(db: sqlite3.Connection) -> list[tuple[date, int]]:
    return [(date.fromisoformat(row["effective_date"]), row["rate_cents"]) for row in
            db.execute("SELECT * FROM day_rates ORDER BY effective_date")]


def day_rate_on(rates: list[tuple[date, int]], day: date) -> Optional[int]:
    """The rate in force on a day: the latest that has begun, else the earliest; None without any rate."""
    begun = [cents for effective, cents in rates if effective <= day]
    return begun[-1] if begun else (rates[0][1] if rates else None)


# --- Days off ----------------------------------------------------------------------------

def _days_off_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "start_date": row["start_date"], "end_date": row["end_date"],
        "fraction": row["fraction"], "note": row["note"],
        "repeat_weeks": row["repeat_weeks"], "repeat_until": row["repeat_until"],
    }


def list_days_off(database_path: Path) -> list[dict[str, Any]]:
    with connect(database_path) as db:
        return [_days_off_view(row) for row in db.execute("SELECT * FROM days_off ORDER BY start_date, id")]


def add_day_off(
    database_path: Path, start_date: str, end_date: str, fraction: Union[int, float] = 1, note: str = "",
    repeat_weeks: int = 0, repeat_until: Optional[str] = None,
) -> dict[str, Any]:
    """Days off from start to end; with repeat_weeks, the same span again every so many weeks, until repeat_until
    (the last day a repeat may start) or for good."""
    start = _parse_date(start_date, "Start date")
    end = _parse_date(end_date, "End date")
    if end < start:
        raise ValueError("End date cannot be before the start date")
    if isinstance(fraction, bool) or fraction not in (0.5, 1):
        raise ValueError("Fraction must be 1 (whole days) or 0.5 (half days)")
    repeat_weeks = _whole(repeat_weeks, "Repeat every (weeks)", 0, 52)
    if repeat_weeks and (end - start).days >= repeat_weeks * 7:
        raise ValueError("A repeating span must be shorter than the repeat interval")
    until = None
    if repeat_until not in (None, ""):
        if not repeat_weeks:
            raise ValueError("Repeat until needs a repeat interval")
        until = _parse_date(repeat_until, "Repeat until")
        if until < start:
            raise ValueError("Repeat until cannot be before the start date")
    with connect(database_path) as db:
        cursor = db.execute(
            "INSERT INTO days_off(start_date, end_date, fraction, note, repeat_weeks, repeat_until) VALUES (?, ?, ?, ?, ?, ?)",
            (start.isoformat(), end.isoformat(), fraction, (note or "").strip(), repeat_weeks,
             None if until is None else until.isoformat()),
        )
        return _days_off_view(db.execute("SELECT * FROM days_off WHERE id = ?", (cursor.lastrowid,)).fetchone())


def delete_day_off(database_path: Path, item_id: int) -> None:
    with connect(database_path) as db:
        if db.execute("DELETE FROM days_off WHERE id = ?", (item_id,)).rowcount == 0:
            raise LookupError("Day off not found")


def load_days_off(db: sqlite3.Connection, through: date) -> list[tuple[date, date, float]]:
    """Every span of days off, with repeating ones expanded up to `through`."""
    spans = []
    for row in db.execute("SELECT * FROM days_off"):
        start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
        fraction, step = float(row["fraction"]), row["repeat_weeks"]
        if not step:
            spans.append((start, end, fraction))
            continue
        last = min(through, date.fromisoformat(row["repeat_until"])) if row["repeat_until"] else through
        shift = timedelta(weeks=step)
        while start <= last:
            spans.append((start, end, fraction))
            start, end = start + shift, end + shift
    return spans


# --- Calendar ----------------------------------------------------------------------------

def easter(year: int) -> date:
    """Easter Sunday by the anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def public_holidays(year: int) -> set[date]:
    """Belgian public holidays of a year."""
    paques = easter(year)
    return {
        date(year, 1, 1), paques + timedelta(days=1), date(year, 5, 1), paques + timedelta(days=39),
        paques + timedelta(days=50), date(year, 7, 21), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25),
    }


def working_days(year: int, month: int) -> list[date]:
    """Monday to Friday of a month, minus public holidays."""
    holidays = public_holidays(year)
    days = (date(year, month, number) for number in range(1, calendar.monthrange(year, month)[1] + 1))
    return [day for day in days if day.weekday() < 5 and day not in holidays]


def last_working_day(year: int, month: int) -> date:
    return working_days(year, month)[-1]


def days_worked(year: int, month: int, days_off: list[tuple[date, date, float]]) -> float:
    """Working days of a month minus days off, each counting as its fraction and only on working days."""
    total = 0.0
    for day in working_days(year, month):
        off = sum(fraction for start, end, fraction in days_off if start <= day <= end)
        total += max(0.0, 1 - off)
    return total
