"""The director loan: the Entity transfers Business makes to Personal from the loan start, summed up as a loan,
with simple interest that Personal pays Business. Money is integer cents; interest is actual/365 on the daily
balance, never on a negative one, and not added to the balance.

The liquidation reserve payouts that later pay off the loan are entries in the company's books, not bank
transfers, so they are not part of this.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

from .database import connect, format_money
from .forecast import _balances, _Context, _load, _month_end, _project, _role, _round_cents
from .forecast_settings import load_settings
from .planned import add_planned, link_planned
from .recurring import _signed_cents

INTEREST_NOTE = "Director loan interest"


def _projected(context: _Context, lines: dict[str, Any], entity: str, category_id: int) -> list[tuple[date, int]]:
    """The entity's projected lines in the category: in the current month only those after the as-of date, as the
    balance walk counts them."""
    as_of = context.entities[entity].as_of
    found = []
    for month, month_lines in lines[entity].items():
        for line in month_lines:
            if line["category_id"] != category_id:
                continue
            if month == context.current and as_of is not None and line["date"] is not None and line["date"] <= as_of:
                continue
            found.append((line["date"] or _month_end(month), line["amount"]))
    return found


def _accrued(movements: list[tuple[date, int]], rate: Decimal, start: date, end: date) -> dict[int, Decimal]:
    """Interest per calendar year from start to end (inclusive) on the running balance, each movement counting
    from its own date."""
    changes: dict[date, int] = {}
    for day, cents in movements:
        changes[max(day, start)] = changes.get(max(day, start), 0) + cents
    points = sorted(changes)
    interest: dict[int, Decimal] = {}
    balance = 0
    for position, day in enumerate(points):
        balance += changes[day]
        until = points[position + 1] if position + 1 < len(points) else end + timedelta(days=1)
        segment = day
        while segment < until and segment <= end:
            year_end = date(segment.year, 12, 31)
            stop = min(until, year_end + timedelta(days=1), end + timedelta(days=1))
            if balance > 0:
                interest[segment.year] = interest.get(segment.year, Decimal(0)) + \
                    Decimal(balance) * rate / 100 * (stop - segment).days / 365
            segment = stop
    return interest


def director_loan(database_path: Path, today: Optional[date] = None) -> dict[str, Any]:
    """The loan's movements with their running balance, and per year: the transfers, the year-end balance, the
    interest accrued and the interest paid, planned and still unpaid."""
    context = _load(database_path, today or date.today())
    lines, _, _ = _project(context)
    _balances(context, lines, {name: [] for name in context.entities})
    settings = context.settings
    start = date.fromisoformat(settings["loan_start"])
    rate = Decimal(str(settings["loan_interest_rate"]))
    horizon_end = _month_end(context.last)
    transfer_cid, interest_cid = _role(context, "transfer_personal"), _role(context, "loan_interest_business")
    notes = []
    movements: list[dict[str, Any]] = []
    if transfer_cid is None:
        notes.append("Choose the Personal Entity transfer category in the settings to follow the loan.")
    else:
        movements += [
            {"date": t["date"], "cents": t["cents"], "projected": False}
            for t in context.entities["personal"].transactions if t["category_id"] == transfer_cid and t["date"] >= start
        ]
        movements += [
            {"date": day, "cents": cents, "projected": True}
            for day, cents in _projected(context, lines, "personal", transfer_cid) if day >= start
        ]
    movements.sort(key=lambda m: (m["date"], m["projected"]))
    payments: list[dict[str, Any]] = []
    if interest_cid is None or _role(context, "loan_interest_personal") is None:
        notes.append("Choose the Director loan interest categories (Personal and Business) in the settings to plan interest.")
    if interest_cid is not None:
        payments += [
            {"date": t["date"], "cents": t["cents"], "projected": False}
            for t in context.entities["business"].transactions if t["category_id"] == interest_cid and t["date"] >= start
        ]
        payments += [
            {"date": day, "cents": cents, "projected": True}
            for day, cents in _projected(context, lines, "business", interest_cid)
        ]
    accrued = _accrued([(m["date"], m["cents"]) for m in movements], rate, start, horizon_end)
    to_date = _accrued([(m["date"], m["cents"]) for m in movements if not m["projected"]], rate, start, context.today)

    years = []
    balance = unpaid = 0
    for year in range(start.year, context.last // 12 + 1):
        of_year = [m for m in movements if m["date"].year == year]
        booked = sum(m["cents"] for m in of_year if not m["projected"])
        projected = sum(m["cents"] for m in of_year if m["projected"])
        interest = _round_cents(accrued.get(year, Decimal(0)))
        paid = sum(p["cents"] for p in payments if p["date"].year == year and not p["projected"])
        planned = sum(p["cents"] for p in payments if p["date"].year == year and p["projected"])
        opening, balance = balance, balance + booked + projected
        unpaid += interest - paid - planned
        years.append({
            "year": str(year), "opening": format_money(opening), "transfers": format_money(booked),
            "transfers_projected": format_money(projected), "closing": format_money(balance),
            "interest": format_money(interest), "interest_paid": format_money(paid),
            "interest_planned": format_money(planned), "interest_unpaid": format_money(unpaid),
        })
    running = 0
    listed = []
    for movement in movements:
        running += movement["cents"]
        listed.append({
            "date": movement["date"].isoformat(), "amount": format_money(movement["cents"]),
            "projected": movement["projected"], "balance": format_money(running),
        })
    return {
        "start": start.isoformat(), "rate": settings["loan_interest_rate"],
        "balance": format_money(sum(m["cents"] for m in movements if not m["projected"])),
        "interest_to_date": format_money(_round_cents(sum(to_date.values(), Decimal(0)))),
        "interest_paid": format_money(sum(p["cents"] for p in payments if not p["projected"])),
        "years": years, "movements": listed,
        "payments": [
            {"date": p["date"].isoformat(), "amount": format_money(p["cents"]), "projected": p["projected"]}
            for p in sorted(payments, key=lambda p: p["date"])
        ],
        "notes": notes,
    }


def plan_loan_interest(
    database_path: Path, day: str, amount: Union[str, int, float], note: Optional[str] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """A linked pair of planned items for an interest payment: Personal pays, Business receives. Returns the loan."""
    cents = abs(_signed_cents(amount))
    with connect(database_path) as db:
        roles = load_settings(db)["roles"]
    business_cid, personal_cid = roles["loan_interest_business"], roles["loan_interest_personal"]
    if business_cid is None or personal_cid is None:
        raise ValueError("Choose the Director loan interest categories (Personal and Business) in the settings first")
    text = (note or "").strip() or INTEREST_NOTE
    business = add_planned(database_path, "business", day, format_money(cents), business_cid, text)
    personal = add_planned(database_path, "personal", day, format_money(-cents), personal_cid, text)
    link_planned(database_path, "personal", personal["id"], business["id"])
    return director_loan(database_path, today)
