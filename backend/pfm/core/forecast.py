"""The forecast engine: each entity projected month by month from today to December of next year.

Money is integer cents throughout; only the returned dictionaries are formatted. A month is
identified by its index, year * 12 + month - 1.
"""

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median
from typing import Any, NamedTuple, Optional

from .database import ACCOUNT_TYPES, UNCATEGORIZED, connect, format_money, masked_iban
from .forecast_settings import (
    ROLES,
    day_rate_on,
    days_worked,
    last_working_day,
    load_day_rates,
    load_days_off,
    load_salary,
    load_settings,
    salary_for_year,
    working_days,
)
from .planned import load_planned
from .recurring import PERIOD_MONTHS, _add_months, _round_half_away, confirmed_items, scheduled_cents
from .reimbursements import load_open_claims, load_repayment_ids
from .reports import COUNTED_SQL, _account_balances, report_flow_type
from .rules import transaction_texts
from .transactions import _alike_key

BACKTEST_ORIGINS = 9  # months before the current month that serve as forecast origins
BACKTEST_MIN_HISTORY = 6  # complete months of history an origin needs
BACKTEST_HORIZONS = 6
KIDS_MIN_ORIGINS = 3
VARIANTS = ((6, False), (12, True))  # (baseline_months, baseline_weighted)
RECOMMEND_HORIZON = 3
CHECK_MONTHS = 12  # months of work the consulting payment check looks back
LATE_GRACE_DAYS = 7  # days past a due date before a missing payment counts as late
VAT_WINDOW_DAYS = 45  # a VAT payment counts for a month up to this long after its due date
TOLERANCE_CENTS = 100
FIRST_DAYS_OF_MONTH = 7  # a consulting payment this early in a month was meant for the previous one
REPAYMENT_MATCH_DAYS = 5  # a Business payment this close to a linked repayment is its other side
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- Months ------------------------------------------------------------------------------

def _index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _month_start(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def _month_end(index: int) -> date:
    year, month = index // 12, index % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_key(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# --- Loading -----------------------------------------------------------------------------

@dataclass
class _Entity:
    categories: dict[int, tuple[str, bool]]  # id -> (name, is_income)
    uncategorized: int
    transactions: list[dict[str, Any]]  # counted ones, oldest first
    items: list[dict[str, Any]]  # confirmed recurring items
    planned: list[dict[str, Any]]
    accounts: list[dict[str, Any]]  # name, balance in cents, as-of date

    @property
    def as_of(self) -> Optional[date]:
        return max((account["as_of"] for account in self.accounts), default=None)


@dataclass
class _Context:
    today: date
    current: int  # index of today's month
    last: int  # index of December next year
    settings: dict[str, Any]
    salary: dict[int, tuple[int, int]]
    rates: list[tuple[date, int]]
    days_off: list[tuple[date, date, float]]
    claims: list[dict[str, Any]] = field(default_factory=list)  # open reimbursement claims
    repayments: dict[str, set[int]] = field(default_factory=dict)  # entity -> transactions of linked repayments
    entities: dict[str, _Entity] = field(default_factory=dict)

    @property
    def months(self) -> range:
        return range(self.current, self.last + 1)


def _load_transactions(db: Any, account_type: str, uncategorized: int) -> list[dict[str, Any]]:
    rows = db.execute(
        f"""
        SELECT t.id, t.booking_date, t.amount_cents, t.category_id, t.merchant, t.description,
               t.reference, t.source_json, a.bank
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = ? AND t.deleted_at IS NULL AND {COUNTED_SQL}
        ORDER BY t.booking_date, t.id
        """,
        (account_type,),
    ).fetchall()
    transactions = []
    for row in rows:
        texts = transaction_texts(dict(row), row["bank"])
        transactions.append({
            "id": row["id"], "date": date.fromisoformat(row["booking_date"]), "cents": int(row["amount_cents"]),
            "category_id": row["category_id"] or uncategorized, "merchant": texts["merchant"], "key": _alike_key(texts),
        })
    return transactions


def _load_entity(db: Any, account_type: str, today: date, horizon_end: date) -> _Entity:
    categories = {
        row["id"]: (row["name"], bool(row["is_income"]))
        for row in db.execute("SELECT id, name, is_income FROM categories WHERE account_type = ?", (account_type,))
    }
    uncategorized = next((cid for cid, (name, _) in categories.items() if name == UNCATEGORIZED), 0)
    transactions = _load_transactions(db, account_type, uncategorized)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for transaction in transactions:
        groups.setdefault(transaction["key"], []).append(transaction)
    accounts = [
        {"name": masked_iban(account.iban), "balance_cents": account.balances[-1], "as_of": date.fromisoformat(account.dates[-1])}
        for account in _account_balances(db, account_type)
    ]
    return _Entity(
        categories, uncategorized, transactions, confirmed_items(db, account_type, today, groups),
        load_planned(db, account_type, today, horizon_end), accounts,
    )


def _linked_repayments(context: _Context, paid_ids: list[int]) -> dict[str, set[int]]:
    """The transactions of repayments linked to a claim, per entity: the Personal one and, in Business, the
    outgoing payment of the same amount nearest in date (within a few days), one per repayment."""
    found: dict[str, set[int]] = {name: set() for name in ACCOUNT_TYPES}
    wanted = set(paid_ids)
    business = context.entities["business"].transactions
    for transaction in context.entities["personal"].transactions:
        if transaction["id"] not in wanted:
            continue
        found["personal"].add(transaction["id"])
        near = [t for t in business if t["cents"] == -transaction["cents"] and t["id"] not in found["business"]
                and abs((t["date"] - transaction["date"]).days) <= REPAYMENT_MATCH_DAYS]
        if near:
            found["business"].add(min(near, key=lambda t: (abs((t["date"] - transaction["date"]).days), t["id"]))["id"])
    return found


def _load(database_path: Path, today: date) -> _Context:
    horizon_end = date(today.year + 1, 12, 31)
    with connect(database_path) as db:
        context = _Context(
            today, _index(today), _index(horizon_end), load_settings(db), load_salary(db),
            load_day_rates(db), load_days_off(db, horizon_end),
        )
        context.entities = {name: _load_entity(db, name, today, horizon_end) for name in ACCOUNT_TYPES}
        context.claims = load_open_claims(db, context.settings["reimbursement_delay_days"])
        context.repayments = _linked_repayments(context, load_repayment_ids(db))
    return context


# --- Lines -------------------------------------------------------------------------------

Lines = dict[str, dict[int, list[dict[str, Any]]]]  # entity -> month index -> projected lines


def _add(
    lines: Lines, entity: str, month: int, category_id: int, cents: int, source: str,
    day: Optional[date], detail: str,
) -> None:
    lines[entity].setdefault(month, []).append(
        {"category_id": category_id, "amount": cents, "source": source, "date": day, "detail": detail}
    )


def _role(context: _Context, role: str) -> Optional[int]:
    return context.settings["roles"][role]


def _recurring_lines(context: _Context, lines: Lines) -> None:
    """Layer 1: each confirmed item's occurrences from its next date, at the schedule amount in force.

    Occurrences before the current month are dropped (a stopped item's overdue ones).
    """
    horizon_end = _month_end(context.last)
    for entity, data in context.entities.items():
        for item in data.items:
            if item["next_date"] is None:
                continue
            first = date.fromisoformat(item["next_date"])
            end = date.fromisoformat(item["end_date"]) if item["end_date"] else None
            step, day, count = PERIOD_MONTHS[item["cadence"]], item["day_of_month"] or first.day, 0
            while True:
                when = first if count == 0 else _add_months(first.replace(day=1), count * step, day)
                count += 1
                if when > horizon_end or (end is not None and when > end):
                    break
                # From the current month on: the next date follows the last booked payment, so an earlier date
                # this month is one not imported yet (due after the latest import), not one already paid.
                if when >= _month_start(context.current):
                    _add(
                        lines, entity, _index(when), item["category_id"] or data.uncategorized,
                        scheduled_cents(item["_schedule"], when), "recurring", when, item["merchant"],
                    )


def _planned_lines(context: _Context, lines: Lines) -> None:
    """Layer 2: planned items from today on, in their month."""
    for entity, data in context.entities.items():
        for planned in data.planned:
            _add(
                lines, entity, _index(planned["date"]), planned["category_id"] or data.uncategorized,
                planned["cents"], "planned", planned["date"], planned["note"],
            )


def _reimbursement_lines(context: _Context, lines: Lines) -> None:
    """Layer 2b: each open reimbursement claim as a linked pair in its repayment month: Business pays, Personal
    receives. A date already past moves to today."""
    horizon_end = _month_end(context.last)
    business, personal = context.entities["business"], context.entities["personal"]
    for claim in context.claims:
        when = max(claim["expected"], context.today)
        if when > horizon_end:
            continue
        month = _index(when)
        _add(lines, "business", month, claim["business_category_id"] or business.uncategorized, -claim["cents"],
             "reimbursement", when, claim["description"])
        _add(lines, "personal", month, claim["personal_category_id"] or personal.uncategorized, claim["cents"],
             "reimbursement", when, claim["description"])


def _euros(cents: int) -> str:
    return f"€{cents // 100}" if cents % 100 == 0 else f"€{cents / 100:.2f}"


def _work_month(payment: date, delay: int) -> int:
    """The month of work a consulting payment pays for; an early-month payment belongs to the month before."""
    return _index(payment) - (1 if payment.day <= FIRST_DAYS_OF_MONTH else 0) - delay


def _consulting_payments(context: _Context) -> list[dict[str, Any]]:
    cid = _role(context, "consulting_income")
    return [t for t in context.entities["business"].transactions if t["category_id"] == cid and t["cents"] > 0]


def _vat_cents(payment_cents: int, vat_rate: float) -> int:
    return _round_cents(Decimal(payment_cents) * Decimal(str(vat_rate)) / (100 + Decimal(str(vat_rate))))


def _vat_date(context: _Context, payment: date) -> date:
    settings = context.settings
    due = _add_months(payment.replace(day=1), settings["vat_delay_months"], settings["vat_day"])
    return due if due >= _month_start(context.current) else context.today


def _consulting_lines(context: _Context, lines: Lines, assumptions: list[str]) -> None:
    """Layers 3 and 4: projected consulting payments, and the VAT on them and on the latest actual one."""
    cid, settings = _role(context, "consulting_income"), context.settings
    if cid is None:
        return
    if not context.rates:
        assumptions.append("No day rate is set, so no consulting income is projected.")
        return
    paid = _consulting_payments(context)
    delay = settings["payment_delay_months"]
    start = max((_work_month(t["date"], delay) for t in paid), default=context.current - delay - 1) + 1
    vat_cid = _role(context, "vat")
    payments: list[tuple[date, int]] = []
    for work in range(start, context.last - delay + 1):
        year, month = work // 12, work % 12 + 1
        days = days_worked(year, month, context.days_off)
        rate = day_rate_on(context.rates, date(year, month, 1))
        invoice = _round_cents(Decimal(str(days)) * rate * (100 + Decimal(str(settings["vat_rate"]))) / 100)
        if invoice <= 0:
            continue
        pay_month = work + delay
        paid_on = max(last_working_day(pay_month // 12, pay_month % 12 + 1), context.today)
        payments.append((paid_on, invoice))
        _add(lines, "business", _index(paid_on), cid, invoice, "consulting", paid_on,
             f"{MONTH_NAMES[month - 1]} {year}: {days:g} days × {_euros(rate)}")
    if vat_cid is None or settings["vat_rate"] <= 0:
        return
    if paid:
        latest = paid[-1]
        vat_booked = any(t["date"] > latest["date"] and t["category_id"] == vat_cid
                         for t in context.entities["business"].transactions)
        if not vat_booked:
            payments.insert(0, (latest["date"], latest["cents"]))
    for paid_on, cents in payments:
        due = _vat_date(context, paid_on)
        if due <= _month_end(context.last):
            _add(lines, "business", _index(due), vat_cid, -_vat_cents(cents, settings["vat_rate"]), "vat", due,
                 f"VAT on the {paid_on.isoformat()} payment")


def _salary_net_percent(context: _Context) -> Optional[float]:
    """The share of the salary that reached Personal over the last 12 complete months, or None without history.

    Business books the full cost; Personal receives it after withholding tax and contributions.
    """
    business_cid, personal_cid = _role(context, "salary_business"), _role(context, "salary_personal")
    window = range(context.current - 12, context.current)
    totals = {
        entity: {month: sum(t["cents"] for t in context.entities[entity].transactions
                            if t["category_id"] == cid and _index(t["date"]) == month) for month in window}
        for entity, cid in (("business", business_cid), ("personal", personal_cid))
    }
    paid = [month for month in window if totals["business"][month] < 0 and totals["personal"][month] > 0]
    cost = -sum(totals["business"][month] for month in paid)
    return round(100 * sum(totals["personal"][month] for month in paid) / cost, 1) if cost else None


def _salary_lines(context: _Context, lines: Lines, assumptions: list[str]) -> None:
    """Layer 5: the monthly salary on each month's last day, an expense in Business and its net share income in Personal."""
    share = context.settings["salary_net_percent"]
    if not share:
        derived = _salary_net_percent(context)
        share = derived if derived is not None else 100
        if derived is not None and any(salary_for_year(context.salary, month // 12) for month in context.months):
            assumptions.append(f"Personal receives {share:g}% of the salary, as over the last 12 months.")
    roles = {"business": (_role(context, "salary_business"), -1), "personal": (_role(context, "salary_personal"), share / 100)}
    repeated: set[int] = set()
    for month in context.months:
        schedule = salary_for_year(context.salary, month // 12)
        if schedule is None:
            continue
        source, cents, last_month, minimum = schedule
        # After the last month only the minimum is paid; it is what reaches Personal, so no net share applies.
        after = month % 12 + 1 > last_month
        if after and not minimum:
            continue
        if source != month // 12 and month // 12 not in repeated:
            repeated.add(month // 12)
            assumptions.append(f"Salary {month // 12} repeats the {source} schedule.")
        for entity, (cid, sign) in roles.items():
            if cid is None or (month == context.current and _booked_this_month(context, entity, cid)):
                continue
            if after:
                amount = -minimum if sign == -1 else minimum
            else:
                amount = -cents if sign == -1 else _round_cents(Decimal(cents) * Decimal(str(sign)))
            _add(lines, entity, month, cid, amount, "salary", _month_end(month), "Monthly minimum salary" if after else "Monthly salary")


def _booked_this_month(context: _Context, entity: str, category_id: int) -> bool:
    return any(t["category_id"] == category_id and _index(t["date"]) == context.current
               for t in context.entities[entity].transactions)


# --- Baselines ---------------------------------------------------------------------------

@dataclass
class _Index:
    """The transactions a baseline may learn from, by category and month."""

    by_category: dict[int, dict[int, list[dict[str, Any]]]]
    first: Optional[int]  # month of the entity's first transaction


class _Base(NamedTuple):
    cents: int
    threshold: Optional[float]  # a transaction above it is a one-off
    one_offs: list[dict[str, Any]]


def _baseline_index(context: _Context, entity: str) -> _Index:
    """Counted transactions minus closed and role categories and minus confirmed recurring items' groups."""
    data = context.entities[entity]
    skipped = set(context.settings["closed_categories"][entity]) | {
        cid for role, cid in context.settings["roles"].items() if cid is not None and ROLES[role][0] == entity
    }
    starts = {item["_key"]: date.fromisoformat(item["start_date"]) for item in data.items}
    by_category: dict[int, dict[int, list[dict[str, Any]]]] = {}
    for transaction in data.transactions:
        start = starts.get(transaction["key"])
        if (transaction["category_id"] in skipped or transaction["id"] in context.repayments.get(entity, ())
                or (start is not None and transaction["date"] >= start)):
            continue
        by_category.setdefault(transaction["category_id"], {}).setdefault(_index(transaction["date"]), []).append(transaction)
    first = _index(data.transactions[0]["date"]) if data.transactions else None
    return _Index(by_category, first)


def _weighted_median(values: list[int], weights: list[int]) -> int:
    running, half = 0, sum(weights) / 2
    for value, weight in sorted(zip(values, weights)):
        running += weight
        if running >= half:
            return value
    return 0


def _category_baselines(
    index: _Index, end: int, months: int, weighted: bool, factor: float
) -> tuple[dict[int, _Base], int]:
    """Each category's baseline from the complete months before month `end`, and how many months were used.

    The baseline is the median monthly total, or the recency-weighted median over 12 months. A transaction
    above `factor` times the median absolute monthly total is a one-off and left out.
    """
    window = [] if index.first is None else [
        month for month in range(end - 1, end - 1 - (12 if weighted else months), -1) if month > index.first
    ]
    weights = [13 - back for back in range(1, len(window) + 1)]
    bases: dict[int, _Base] = {}
    for category, by_month in index.by_category.items():
        totals = [sum(t["cents"] for t in by_month.get(month, ())) for month in window]
        typical = median(abs(total) for total in totals) if totals else 0
        threshold = factor * typical if typical > 0 else None
        one_offs = [t for month in window for t in by_month.get(month, ()) if threshold is not None and abs(t["cents"]) > threshold]
        skipped = {t["id"] for t in one_offs}
        kept = [sum(t["cents"] for t in by_month.get(month, ()) if t["id"] not in skipped) for month in window]
        if not any(kept):
            continue
        cents = _weighted_median(kept, weights) if weighted else _round_half_away(median(kept))
        bases[category] = _Base(cents, threshold, one_offs)
    return bases, len(window)


def _current_baselines(context: _Context, index: _Index) -> tuple[dict[int, _Base], int]:
    settings = context.settings
    return _category_baselines(
        index, context.current, settings["baseline_months"], settings["baseline_weighted"], settings["one_off_factor"]
    )


def _baseline_lines(context: _Context, entity: str, index: _Index, lines: Lines) -> None:
    """Layer 6: the baseline every month; in the current month what is not yet booked, never past zero."""
    bases, used = _current_baselines(context, index)
    detail = f"{'Weighted median' if context.settings['baseline_weighted'] else 'Median'} of the last {used} months"
    for category, base in sorted(bases.items()):
        for month in context.months:
            cents = base.cents
            if month == context.current:
                booked = sum(t["cents"] for t in index.by_category[category].get(month, ()))
                cents = base.cents - booked
                cents = min(cents, 0) if base.cents < 0 else max(cents, 0)
            if cents:
                _add(lines, entity, month, category, cents, "baseline", None, detail)


def _one_offs(context: _Context, entity: str, index: _Index) -> list[dict[str, Any]]:
    data = context.entities[entity]
    bases, _ = _current_baselines(context, index)
    found = sorted((t for base in bases.values() for t in base.one_offs), key=lambda t: (t["date"], t["id"]), reverse=True)
    return [
        {"transaction_id": t["id"], "date": t["date"].isoformat(), "merchant": t["merchant"],
         "category": data.categories[t["category_id"]][0], "amount": format_money(t["cents"])}
        for t in found
    ]


# --- Balances and transfers --------------------------------------------------------------

def _counted_net(context: _Context, entity: str, month: int, projected: list[dict[str, Any]]) -> int:
    """What the month's projected lines add to the balance: in the current month, only those after the as-of date."""
    as_of = context.entities[entity].as_of
    if month == context.current and as_of is not None:
        projected = [line for line in projected if line["source"] == "baseline" or line["date"] > as_of]
    return sum(line["amount"] for line in projected)


def _transfer_due(context: _Context, month: int) -> bool:
    """Entity transfers top Personal up in every month, on top of any salary, except where one is already booked.

    The salary lines are in place before the walk, so a transfer only fills what the salary leaves short.
    """
    cid = _role(context, "transfer_business")
    return not (month == context.current and _booked_this_month(context, "business", cid))


def _balances(
    context: _Context, lines: Lines, assumptions: dict[str, list[str]]
) -> tuple[dict[str, dict[int, int]], dict[str, dict[int, list[str]]]]:
    """Walk the months with running balances, adding the Personal and Business transfers as they are needed."""
    settings = context.settings
    business_role, personal_role = _role(context, "transfer_business"), _role(context, "transfer_personal")
    transfers = business_role is not None and personal_role is not None
    if not transfers:
        for entity in ("business", "personal"):
            assumptions[entity].append("Entity transfers need the transfer categories chosen in the settings; none are projected.")
    running = {name: sum(a["balance_cents"] for a in data.accounts) for name, data in context.entities.items()}
    closings: dict[str, dict[int, int]] = {name: {} for name in ACCOUNT_TYPES}
    flags: dict[str, dict[int, list[str]]] = {name: {} for name in ACCOUNT_TYPES}
    for month in context.months:
        for name in ACCOUNT_TYPES:
            running[name] += _counted_net(context, name, month, lines[name].get(month, []))
        if transfers and _transfer_due(context, month):
            need = max(0, settings["personal_min_balance"] - running["personal"])
            give = max(0, running["business"] - settings["business_buffer"])
            amount = min(need, give)
            if amount:
                day = _month_end(month)
                _add(lines, "business", month, business_role, -amount, "transfer", day, "Transfer to Personal")
                _add(lines, "personal", month, personal_role, amount, "transfer", day, "Transfer from Business")
                running["business"] -= amount
                running["personal"] += amount
            if need > give:
                for name in ("business", "personal"):
                    flags[name].setdefault(month, []).append("transfer_short")
        for name in ACCOUNT_TYPES:
            closings[name][month] = running[name]
    return closings, flags


# --- Backtest ----------------------------------------------------------------------------

def _backtest_errors(context: _Context, index: _Index, variant: tuple[int, bool]) -> dict[int, list[int]]:
    """Per horizon, each origin's cumulative error (actual minus baseline) over the baseline categories."""
    months, weighted = variant
    errors: dict[int, list[int]] = {}
    for origin in _origins(context, index):
        bases, _ = _category_baselines(index, origin, months, weighted, context.settings["one_off_factor"])
        cumulative = 0
        for horizon in range(1, BACKTEST_HORIZONS + 1):
            month = origin + horizon - 1
            if month >= context.current:
                break
            for category, by_month in index.by_category.items():
                base = bases.get(category, _Base(0, None, []))
                actual = sum(t["cents"] for t in by_month.get(month, ()) if base.threshold is None or abs(t["cents"]) <= base.threshold)
                cumulative += actual - base.cents
            errors.setdefault(horizon, []).append(cumulative)
    return errors


def _origins(context: _Context, index: _Index) -> list[int]:
    """The months before the current one whose forecasts can be replayed: 6 complete months of history before them."""
    if index.first is None:
        return []
    return [o for o in range(context.current - BACKTEST_ORIGINS, context.current) if o - index.first - 1 >= BACKTEST_MIN_HISTORY]


def _percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    position = (len(ordered) - 1) * share
    low, high = math.floor(position), math.ceil(position)
    return _round_half_away(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def _mean_abs(values: list[int]) -> int:
    return _round_half_away(sum(abs(v) for v in values) / len(values))


def _consulting_check(context: _Context) -> list[dict[str, Any]]:
    """For each actual consulting payment, the days it implies at that month's rate against the working days."""
    settings, checks = context.settings, []
    for payment in _consulting_payments(context):
        work = _work_month(payment["date"], settings["payment_delay_months"])
        rate = day_rate_on(context.rates, _month_start(work))
        available = len(working_days(work // 12, work % 12 + 1))
        inferred = None if rate is None else round(
            payment["cents"] / (1 + settings["vat_rate"] / 100) / rate * 2) / 2
        checks.append({
            "payment_date": payment["date"].isoformat(), "month_of_work": _month_key(work),
            "days_inferred": inferred, "working_days": available,
            "plausible": None if inferred is None else inferred <= available,
        })
    return checks


def _backtest_entity(context: _Context, entity: str, index: _Index) -> dict[str, Any]:
    settings = context.settings
    chosen = (settings["baseline_months"], settings["baseline_weighted"])
    origins = _origins(context, index)
    errors = _backtest_errors(context, index, chosen)
    if entity == "kids" and len(origins) < KIDS_MIN_ORIGINS:
        errors = {}
    scores: dict[tuple[int, bool], Optional[int]] = {}
    for variant in dict.fromkeys((*VARIANTS, chosen)):
        replayed = errors if variant == chosen else _backtest_errors(context, index, variant)
        at_horizon = replayed.get(RECOMMEND_HORIZON)
        scores[variant] = _mean_abs(at_horizon) if at_horizon else None
    scored = [(score, variant) for variant, score in scores.items() if score is not None and variant in VARIANTS]
    best = min(scored, key=lambda pair: pair[0])[1] if scored else chosen
    return {
        "origins": len(origins), "errors": errors,
        "baseline": {"baseline_months": chosen[0], "baseline_weighted": chosen[1]},
        "recommended": {"baseline_months": best[0], "baseline_weighted": best[1]},
        "consulting_check": _consulting_check(context) if entity == "business" else [],
    }


def _band(errors: dict[int, list[int]], horizon: int) -> tuple[int, int]:
    """The 10th and 90th percentile of the cumulative error at a horizon; wider beyond the longest by √(horizon / longest)."""
    if not errors:
        return 0, 0
    longest = max(errors)
    low, high = (_percentile(errors[min(horizon, longest)], share) for share in (0.1, 0.9))
    if horizon > longest:
        scale = math.sqrt(horizon / longest)
        low, high = _round_half_away(low * scale), _round_half_away(high * scale)
    return low, high


def _run_backtests(context: _Context, indexes: dict[str, _Index]) -> dict[str, dict[str, Any]]:
    return {name: _backtest_entity(context, name, indexes[name]) for name in ACCOUNT_TYPES}


def _format_backtest(result: dict[str, Any]) -> dict[str, Any]:
    horizons = [
        {"months": horizon, "p10": format_money(_percentile(errors, 0.1)), "p90": format_money(_percentile(errors, 0.9)),
         "mae": format_money(_mean_abs(errors))}
        for horizon, errors in sorted(result["errors"].items())
    ]
    return {"origins": result["origins"], "horizons": horizons, "baseline": result["baseline"],
            "recommended": result["recommended"]}


def backtest(database_path: Path, today: Optional[date] = None) -> dict[str, dict[str, Any]]:
    """Per entity: the replayed origins, the error percentiles and mean error per horizon, the recommended
    baseline variant and, for Business, the consulting plausibility check."""
    context = _load(database_path, today or date.today())
    results = _run_backtests(context, {name: _baseline_index(context, name) for name in ACCOUNT_TYPES})
    return {name: {**_format_backtest(result), "consulting_check": result["consulting_check"]}
            for name, result in results.items()}


def _payment_status(received: int, expected: int, fits_rate: bool, due: date, today: date) -> str:
    """`fewer_days`: less than expected but a whole or half number of days at the rate, most likely days off
    that weren't entered; `differs`: more than expected, or an amount that doesn't fit the rate."""
    if abs(received - expected) <= TOLERANCE_CENTS:
        return "paid"
    if received:
        return "fewer_days" if received < expected and fits_rate else "differs"
    return "late" if today > due + timedelta(days=LATE_GRACE_DAYS) else "due"


def _vat_window(context: _Context, received: list[dict[str, Any]]) -> tuple[int, date, date, date]:
    """(expected VAT, latest payment date, due date, last date a VAT payment still counts) of a month's payments."""
    settings = context.settings
    latest = max(p["date"] for p in received)
    expected = _vat_cents(sum(p["cents"] for p in received), settings["vat_rate"])
    due = _add_months(latest.replace(day=1), settings["vat_delay_months"], settings["vat_day"])
    return expected, latest, due, due + timedelta(days=VAT_WINDOW_DAYS)


def _exact_vat(context: _Context, received: list[dict[str, Any]], pool: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The VAT payment in a month's window for exactly its expected VAT (within the tolerance), taken from `pool`."""
    expected, latest, due, last = _vat_window(context, received)
    exact = [t for t in pool if latest < t["date"] <= last and abs(-t["cents"] - expected) <= TOLERANCE_CENTS]
    if not exact:
        return None
    found = min(exact, key=lambda t: abs((t["date"] - due).days))
    pool.remove(found)
    return found


def _vat_check(
    context: _Context, received: list[dict[str, Any]], pool: list[dict[str, Any]],
    exact: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """A month's VAT: the amount inside its payments, when it is due, and the payments matched to it.
    An `exact` match (found first for every month) is used as is; otherwise the payments nearest the due date
    are taken from `pool`, so one VAT payment serves one month."""
    expected, latest, due, last = _vat_window(context, received)
    if exact is not None:
        matched, paid = [exact], -exact["cents"]
    else:
        window = [t for t in pool if latest < t["date"] <= last]
        window.sort(key=lambda t: (abs((t["date"] - due).days), t["date"]))
        matched, paid = [], 0
        for t in window:
            if paid >= expected - TOLERANCE_CENTS:
                break
            matched.append(t)
            paid += -t["cents"]
        for t in matched:
            pool.remove(t)
    if matched:
        status = "paid" if abs(paid - expected) <= TOLERANCE_CENTS else "differs"
    else:
        status = "missing" if context.today > due + timedelta(days=LATE_GRACE_DAYS) else "due"
    return {
        "expected": format_money(expected), "due": due.isoformat(), "status": status,
        "paid": [{"date": t["date"].isoformat(), "amount": format_money(-t["cents"]), "transaction_id": t["id"]}
                 for t in sorted(matched, key=lambda t: t["date"])],
    }


def consulting_payments(database_path: Path, today: Optional[date] = None) -> dict[str, Any]:
    """Per month of work, the last 12 before the current one: the expected consulting payment against what
    arrived, and the VAT on it. A missing day rate or consulting role gives an empty check."""
    context = _load(database_path, today or date.today())
    settings = context.settings
    summary = {"late": 0, "differs": 0, "fewer_days": 0, "vat_missing": 0}
    cid = _role(context, "consulting_income")
    if cid is None or not context.rates:
        return {"months": [], "summary": summary}
    delay, vat_rate, vat_cid = settings["payment_delay_months"], settings["vat_rate"], _role(context, "vat")
    business = context.entities["business"].transactions
    paid = _consulting_payments(context)
    vat_pool = [] if vat_cid is None else [t for t in business if t["category_id"] == vat_cid and t["cents"] < 0]
    months = []
    worked: list[tuple[int, list[dict[str, Any]]]] = []
    for work in range(context.current - CHECK_MONTHS, context.current):
        received = [t for t in paid if _work_month(t["date"], delay) == work]
        worked.append((work, received))
    # A VAT payment for exactly a month's VAT belongs to that month, before nearest-date matching takes what's left:
    # otherwise an earlier month whose window also covers it would take it.
    use_vat = vat_cid is not None and vat_rate > 0
    exact = {work: _exact_vat(context, received, vat_pool) for work, received in worked if received and use_vat}
    for work, received in worked:
        year, month = work // 12, work % 12 + 1
        rate = day_rate_on(context.rates, date(year, month, 1))
        working = len(working_days(year, month))
        days = days_worked(year, month, context.days_off)
        expected = _round_cents(Decimal(str(days)) * rate * (100 + Decimal(str(vat_rate))) / 100)
        if expected <= 0 and not received:
            continue
        pay_month = work + delay
        due = last_working_day(pay_month // 12, pay_month % 12 + 1)
        total = sum(t["cents"] for t in received)
        days_paid = round(total / (1 + vat_rate / 100) / rate * 2) / 2 if received else 0.0
        fits = received and abs(_round_cents(Decimal(str(days_paid)) * rate * (100 + Decimal(str(vat_rate))) / 100) - total) <= TOLERANCE_CENTS
        status = _payment_status(total, expected, bool(fits), due, context.today)
        vat = _vat_check(context, received, vat_pool, exact.get(work)) if received and use_vat else None
        summary["late"] += status == "late"
        summary["differs"] += status == "differs"
        summary["fewer_days"] += status == "fewer_days"
        summary["vat_missing"] += bool(vat and vat["status"] == "missing")
        months.append({
            "month": _month_key(work), "working_days": working, "days_off": round(working - days, 2),
            "days_expected": days, "expected": format_money(expected), "due": due.isoformat(),
            "received": [{"date": t["date"].isoformat(), "amount": format_money(t["cents"]), "transaction_id": t["id"]}
                         for t in received],
            "days_paid": days_paid,
            "status": status, "vat": vat,
        })
    return {"months": months, "summary": summary}


# --- The forecast ------------------------------------------------------------------------

def _flows(data: _Entity, entries: list[tuple[int, int]]) -> tuple[int, int]:
    """(income, expense) of (category id, signed cents) entries, grouped as the reports do."""
    income = expense = 0
    for category_id, cents in entries:
        name, is_income = data.categories[category_id]
        if report_flow_type(is_income, name, cents) == "income":
            income += cents
        else:
            expense -= cents
    return income, expense


def _entity_slice(
    context: _Context, entity: str, lines: Lines, closings: dict[int, int], flags: dict[int, list[str]],
    backtest_result: dict[str, Any], one_offs: list[dict[str, Any]], assumptions: list[str],
) -> dict[str, Any]:
    data, today = context.entities[entity], context.today
    errors = backtest_result["errors"]
    months, bands = [], {}
    for month in context.months:
        projected = sorted(
            lines[entity].get(month, []),
            key=lambda line: (line["date"] or date.max, data.categories[line["category_id"]][0], line["amount"]),
        )
        booked = [(t["category_id"], t["cents"]) for t in data.transactions if _index(t["date"]) == month]
        actual_income, actual_expense = _flows(data, booked)
        income, expense = _flows(data, booked + [(line["category_id"], line["amount"]) for line in projected])
        low, high = bands[month] = _band(errors, month - context.current + 1)
        months.append({
            "month": _month_key(month), "partial": month == context.current,
            "lines": [
                {"category_id": line["category_id"], "category": data.categories[line["category_id"]][0],
                 "amount": format_money(line["amount"]), "source": line["source"],
                 "date": line["date"].isoformat() if line["date"] else None, "detail": line["detail"]}
                for line in projected
            ],
            "actual": {"income": format_money(actual_income), "expense": format_money(actual_expense)},
            "income": format_money(income), "expense": format_money(expense), "net": format_money(income - expense),
            "closing": format_money(closings[month]), "low": format_money(closings[month] + low),
            "high": format_money(closings[month] + high), "flags": flags.get(month, []),
        })
    year_ends = [
        {"year": str(month // 12), "closing": format_money(closings[month]),
         "low": format_money(closings[month] + bands[month][0]), "high": format_money(closings[month] + bands[month][1])}
        for month in context.months if month % 12 == 11
    ]
    return {
        "account_type": entity, "today": today.isoformat(),
        "accounts": [
            {"name": a["name"], "balance": format_money(a["balance_cents"]), "as_of": a["as_of"].isoformat()}
            for a in data.accounts
        ],
        "opening": format_money(sum(a["balance_cents"] for a in data.accounts)),
        "months": months, "year_ends": year_ends, "one_offs": one_offs, "assumptions": assumptions,
        "backtest": _format_backtest(backtest_result),
    }


def _project(context: _Context) -> tuple[Lines, dict[str, list[str]], dict[str, _Index]]:
    """The projected lines of every entity before the balance walk (so without entity transfers), the assumptions
    so far and the baseline indexes."""
    lines: Lines = {name: {} for name in ACCOUNT_TYPES}
    assumptions: dict[str, list[str]] = {name: [] for name in ACCOUNT_TYPES}
    indexes = {name: _baseline_index(context, name) for name in ACCOUNT_TYPES}
    _recurring_lines(context, lines)
    _planned_lines(context, lines)
    _reimbursement_lines(context, lines)
    _consulting_lines(context, lines, assumptions["business"])
    shared: list[str] = []
    _salary_lines(context, lines, shared)
    for name in ("business", "personal"):
        assumptions[name].extend(shared)
    for name in ACCOUNT_TYPES:
        _baseline_lines(context, name, indexes[name], lines)
    return lines, assumptions, indexes


def forecast(database_path: Path, today: Optional[date] = None) -> dict[str, dict[str, Any]]:
    """Every entity's projection from today's month to December of next year, keyed by account type.

    Entities are computed together because the transfers link Personal and Business.
    """
    context = _load(database_path, today or date.today())
    lines, assumptions, indexes = _project(context)
    closings, flags = _balances(context, lines, assumptions)
    backtests = _run_backtests(context, indexes)
    return {
        name: _entity_slice(
            context, name, lines, closings[name], flags[name], backtests[name],
            _one_offs(context, name, indexes[name]), assumptions[name],
        )
        for name in ACCOUNT_TYPES
    }
