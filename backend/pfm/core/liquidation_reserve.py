"""Business's liquidation reserve: the reserves already formed, the share of each year's profit put into a new
one, the tax on forming it and the payouts that pay down the director loan (art. 184quater WIB 92, small
companies). An estimate from settings to confirm with the accountant, not tax advice. Money is integer cents.

Forming a reserve R costs R x (1 + tax rate) of the profit after corporate tax. A reserve formed on the accounts
of year Y can be paid out at the low rate from 1 January of Y + wait + 1. Payout: Business withholds
gross x rate and credits the net to the director's current account, so no money moves between bank accounts.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from .forecast import _add, _Context, _index, _month_end, _role, _round_cents


def _line_roles(context: _Context) -> tuple[Optional[int], Optional[int]]:
    return _role(context, "reserve_tax"), _role(context, "reserve_withholding")


def project_reserves(context: _Context, lines: dict[str, Any], assumptions: list[str]) -> None:
    """Fill `context.reserves` and add the forming tax and payout withholding lines (source `reserve`).

    Each reserve is {label, gross, rate (Decimal percent), formed (year it counts from; None for an existing one),
    payout (date of the payout, None when after the forecast), withholding}. Without the two role categories
    nothing is added and the assumptions say so.
    """
    from .corporate_tax import _profit, _tax  # imported here: corporate_tax builds on forecast

    settings = context.settings
    context.reserves = []
    existing = settings["liquidation_reserves"]
    share = Decimal(str(settings["reserve_share"]))
    if not existing and share <= 0:
        return
    tax_cid, withholding_cid = _line_roles(context)
    if tax_cid is None or withholding_cid is None:
        assumptions.append(
            "Choose the Liquidation reserve tax and Liquidation reserve withholding categories (Business) in the "
            "settings, or create them, to plan the liquidation reserve; none is projected."
        )
        return
    today = context.today
    horizon_end = _month_end(context.last)
    reserves: list[dict[str, Any]] = []
    for entry in existing:
        payable = date.fromisoformat(entry["payable"])
        reserves.append({
            "label": entry["label"], "gross": entry["amount"], "rate": Decimal(str(entry["rate"])), "formed": None,
            # One already payable is paid out in the current month.
            "payout": payable if payable >= today else _month_end(context.current),
        })
    if share > 0:
        tax_rate = Decimal(str(settings["reserve_tax_rate"]))
        for year in range(today.year, context.last // 12 + 1):
            profit, _, salary = _profit(context, lines, year)
            tax = _tax(settings, profit, salary >= settings["tax_reduced_threshold"])
            after_tax = profit - tax
            if after_tax <= 0:
                continue
            gross = _round_cents(Decimal(after_tax) * share / 100 / (1 + tax_rate / 100))
            if gross <= 0:
                continue
            day = date(year, 12, 20)
            _add(lines, "business", _index(day), tax_cid, -_round_cents(Decimal(gross) * tax_rate / 100), "reserve", day,
                 f"Tax on the {year} liquidation reserve")
            reserves.append({
                "label": f"{year} reserve", "gross": gross, "rate": Decimal(str(settings["reserve_payout_rate"])),
                "formed": year, "payout": date(year + settings["reserve_wait_years"] + 1, 1, 1),
            })
        assumptions.append(
            f"A reserve of {share:g}% of each year's profit after corporate tax is formed, taxed at "
            f"{Decimal(str(settings['reserve_tax_rate'])):g}% on 20 December, payable after {settings['reserve_wait_years']} years."
        )
    for reserve in reserves:
        if reserve["payout"] > horizon_end:
            reserve["payout"] = None
            reserve["withholding"] = 0
            continue
        reserve["withholding"] = _round_cents(Decimal(reserve["gross"]) * reserve["rate"] / 100)
        _add(lines, "business", _index(reserve["payout"]), withholding_cid, -reserve["withholding"], "reserve",
             reserve["payout"], f"Withholding on the liquidation reserve payout{': ' + reserve['label'] if reserve['label'] else ''}")
    context.reserves = reserves


def payouts(context: _Context) -> list[dict[str, Any]]:
    """The payouts inside the forecast as {date, gross, withholding, net, label}, oldest first."""
    found = [
        {"date": r["payout"], "gross": r["gross"], "withholding": r["withholding"],
         "net": r["gross"] - r["withholding"], "label": r["label"]}
        for r in context.reserves if r["payout"] is not None
    ]
    return sorted(found, key=lambda payout: payout["date"])


def outstanding(context: _Context, year: int) -> tuple[int, Decimal]:
    """The gross reserves formed and not yet paid out at the end of the year, and their withholding rate in percent
    weighted by amount (the payout rate when there are none)."""
    end = date(year, 12, 31)
    held = [
        r for r in context.reserves
        if (r["formed"] is None or r["formed"] <= year) and (r["payout"] is None or r["payout"] > end)
    ]
    gross = sum(r["gross"] for r in held)
    if gross <= 0:
        return 0, Decimal(str(context.settings["reserve_payout_rate"]))
    return gross, sum((Decimal(r["gross"]) * r["rate"] for r in held), Decimal(0)) / gross
