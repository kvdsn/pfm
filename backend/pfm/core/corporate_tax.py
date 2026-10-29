"""Business's corporate tax estimate per year, its prepayments and the advice that avoids the surcharge.

An estimate from the owner's tax settings, not tax advice. Money is integer cents; rates are Decimals.
"""

from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

from .database import connect, format_money, parse_money
from .forecast import _Context, _load, _project, _role, _round_cents
from .forecast_settings import load_settings
from .planned import _unlink
from .recurring import _now

# (month, day) of the prepayment deadlines and the multiple of the base rate each earns as a credit.
DEADLINES = ((4, 10), (7, 10), (10, 10), (12, 20))
CREDIT_FACTORS = (Decimal("3"), Decimal("2.5"), Decimal("2"), Decimal("1.5"))
SURCHARGE_FACTOR = Decimal("2.25")
ADVICE_NOTE = "Corporate tax prepayment (advised)"


def _ceil_euro(cents: Decimal) -> int:
    """Cents rounded up to a whole euro."""
    return int((max(cents, Decimal(0)) / 100).to_integral_value(rounding=ROUND_CEILING)) * 100


def _deadline_slot(day: date) -> Optional[int]:
    """The index of the first deadline of the day's year on or after it; None after the last one."""
    for slot, (month, number) in enumerate(DEADLINES):
        if day <= date(day.year, month, number):
            return slot
    return None


def _profit(context: _Context, lines: dict[str, Any], year: int) -> tuple[int, dict[str, int], int]:
    """(profit, per-category result by name, director remuneration as a positive cost) for a calendar year.

    Business's booked counted transactions plus the projected lines of the year's remaining months, without the
    VAT, corporate tax and transfer categories or the excluded ones; consulting income net of VAT.
    """
    data = context.entities["business"]
    settings = context.settings
    left_out = {_role(context, role) for role in ("vat", "corporate_tax", "transfer_business")}
    left_out |= set(settings["tax_profit_excluded"])
    consulting, salary_role = _role(context, "consulting_income"), _role(context, "salary_business")
    entries = [(t["category_id"], t["cents"]) for t in data.transactions if t["date"].year == year]
    entries += [
        (line["category_id"], line["amount"])
        for month, month_lines in lines["business"].items() if month // 12 == year for line in month_lines
    ]
    divisor = 100 + Decimal(str(settings["vat_rate"]))
    breakdown: dict[str, int] = {}
    salary = 0
    for category_id, cents in entries:
        if category_id in left_out:
            continue
        if category_id == consulting:
            cents = _round_cents(Decimal(cents) * 100 / divisor)
        if category_id == salary_role:
            salary -= cents
        name = data.categories[category_id][0]
        breakdown[name] = breakdown.get(name, 0) + cents
    return sum(breakdown.values()), breakdown, salary


def _tax(settings: dict[str, Any], profit: int, eligible: bool) -> int:
    if profit <= 0:
        return 0
    standard = Decimal(str(settings["tax_standard_rate"]))
    if not eligible:
        return _round_cents(Decimal(profit) * standard / 100)
    band = min(profit, settings["tax_reduced_band"])
    reduced = Decimal(str(settings["tax_reduced_rate"]))
    return _round_cents((Decimal(band) * reduced + Decimal(profit - band) * standard) / 100)


def _credit_rate(settings: dict[str, Any], slot: int) -> Decimal:
    """The share of a prepayment credited against the tax, as a fraction."""
    return CREDIT_FACTORS[slot] * Decimal(str(settings["tax_base_rate"])) / 100


def _surcharge(settings: dict[str, Any], tax: int, paid: list[int]) -> int:
    """The surcharge in cents given the amounts paid toward each deadline; 0 when it does not apply."""
    if not settings["tax_surcharge_applies"]:
        return 0
    due = Decimal(tax) * SURCHARGE_FACTOR * Decimal(str(settings["tax_base_rate"])) / 100
    credit = sum((Decimal(amount) * _credit_rate(settings, slot) for slot, amount in enumerate(paid)), Decimal(0))
    return max(0, _round_cents(due - credit))


def _advice(settings: dict[str, Any], tax: int, prepaid: list[int], remaining: list[int]) -> list[int]:
    """The amount to prepay at each deadline (all four slots; 0 for the closed ones).

    The tax not yet prepaid is spread evenly over the remaining deadlines, rounded up to the euro; if that
    leaves a surcharge, the earliest remaining deadline takes more, surcharge ÷ its credit rate rounded up.
    """
    advised = [0, 0, 0, 0]
    if not remaining:
        return advised
    unpaid = max(0, tax - sum(prepaid))
    share = _ceil_euro(Decimal(unpaid) / len(remaining))
    for slot in remaining:
        advised[slot] = share
    left = _surcharge(settings, tax, [p + a for p, a in zip(prepaid, advised)])
    rate = _credit_rate(settings, remaining[0])
    if left > 0 and rate > 0:
        advised[remaining[0]] += _ceil_euro(Decimal(left) / rate)
    return advised


def _estimate(context: _Context, lines: dict[str, Any], year: int) -> dict[str, Any]:
    settings, today = context.settings, context.today
    profit, breakdown, salary = _profit(context, lines, year)
    eligible = salary >= settings["tax_reduced_threshold"]
    tax = _tax(settings, profit, eligible)
    tax_cid = _role(context, "corporate_tax")
    prepaid, planned = [0, 0, 0, 0], [0, 0, 0, 0]
    if tax_cid is not None:
        for t in context.entities["business"].transactions:
            slot = _deadline_slot(t["date"]) if t["date"].year == year and t["category_id"] == tax_cid else None
            if slot is not None:
                prepaid[slot] -= t["cents"]
        for month, month_lines in lines["business"].items():
            for line in month_lines:
                slot = _deadline_slot(line["date"]) if month // 12 == year and line["category_id"] == tax_cid \
                    and line["source"] == "planned" else None
                if slot is not None:
                    planned[slot] -= line["amount"]
    days = [date(year, month, number) for month, number in DEADLINES]
    remaining = [slot for slot, day in enumerate(days) if day >= today]
    advised = _advice(settings, tax, prepaid, remaining)
    assumptions = [f"All corporate tax paid in {year} counts as a prepayment for {year}."]
    if tax_cid is None:
        assumptions.append("No corporate tax category is chosen in the settings, so no prepayments are counted.")
    if not remaining:
        assumptions.append(f"The prepayments for {year} are closed: the last deadline has passed.")
    return {
        "year": str(year), "profit": format_money(profit), "eligible": eligible, "salary": format_money(salary),
        "tax": format_money(tax),
        "breakdown": [
            {"category": name, "amount": format_money(cents)}
            for name, cents in sorted(breakdown.items(), key=lambda pair: (-abs(pair[1]), pair[0]))
        ],
        "deadlines": [
            {"date": day.isoformat(), "prepaid": format_money(prepaid[slot]), "planned": format_money(planned[slot]),
             "advised": format_money(advised[slot]), "past": day < today}
            for slot, day in enumerate(days)
        ],
        "prepaid_total": format_money(sum(prepaid)), "planned_total": format_money(sum(planned)),
        "surcharge_as_planned": format_money(_surcharge(settings, tax, [p + q for p, q in zip(prepaid, planned)])),
        "closed": not remaining, "assumptions": assumptions,
    }


def corporate_tax(database_path: Path, today: Optional[date] = None) -> dict[str, Any]:
    """The tax estimate, prepayments and advice for the current year and the next."""
    context = _load(database_path, today or date.today())
    lines, _, _ = _project(context)
    return {"years": [_estimate(context, lines, year) for year in (context.today.year, context.today.year + 1)]}


def apply_corporate_tax(database_path: Path, year: str, today: Optional[date] = None) -> dict[str, Any]:
    """Replace the year's planned corporate-tax items dated from today on with one per remaining deadline at the
    advised amount; returns the new estimate. Other planned items and ones already past are untouched."""
    today = today or date.today()
    estimate = next((y for y in corporate_tax(database_path, today)["years"] if y["year"] == year), None)
    if estimate is None:
        raise ValueError(f"Corporate tax advice covers {today.year} and {today.year + 1}")
    with connect(database_path) as db:
        cid = load_settings(db)["roles"]["corporate_tax"]
        if cid is None:
            raise ValueError("Choose the corporate tax category in the forecast settings first")
        old = [row["id"] for row in db.execute(
            "SELECT id FROM planned_items WHERE account_type = 'business' AND category_id = ? AND date >= ? AND date <= ?",
            # Only this year's items: from today or 1 January, whichever is later.
            (cid, max(today.isoformat(), f"{year}-01-01"), f"{year}-12-31"),
        )]
        if old:
            _unlink(db, old)
            db.execute(f"DELETE FROM planned_items WHERE id IN ({', '.join('?' for _ in old)})", old)
        stamp = _now()
        for row in estimate["deadlines"]:
            cents = parse_money(row["advised"])
            if row["past"] or cents <= 0:
                continue
            db.execute(
                "INSERT INTO planned_items(account_type, date, amount_cents, category_id, note, created_at, updated_at) "
                "VALUES ('business', ?, ?, ?, ?, ?, ?)",
                (row["date"], -cents, cid, ADVICE_NOTE, stamp, stamp),
            )
    return corporate_tax(database_path, today)
