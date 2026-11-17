"""The ways Business can take money out, compared by the company profit each needs per 1,000 net with the director.

An estimate from the owner's settings, not tax advice. Rates are percentages in the settings and fractions here.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .corporate_tax import corporate_tax
from .database import connect, format_money, parse_money
from .forecast_settings import load_settings

NET = Decimal(1000)


def _rate(settings: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(settings[key])) / 100


def _corporate_rate(settings: dict[str, Any], profit: int, eligible: bool) -> Decimal:
    """The reduced rate when next year is eligible and its profit fits the band, else the standard rate."""
    if eligible and profit <= settings["tax_reduced_band"]:
        return _rate(settings, "tax_reduced_rate")
    return _rate(settings, "tax_standard_rate")


def _row(key: str, net_per_euro: Decimal, extra: Decimal = Decimal(0)) -> dict[str, Any]:
    profit = NET / net_per_euro + extra
    return {
        "key": key, "profit_per_1000": format_money(int((profit * 100).to_integral_value())),
        "tax_share": float(round((1 - NET / profit) * 100, 1)), "net_per_euro": float(round(NET / profit / 1000, 4)),
    }


def compute_routes(settings: dict[str, Any], profit: int, eligible: bool) -> dict[str, Any]:
    """The routes, the corporate rate used and the threshold note, from settings and next year's profit."""
    c = _corporate_rate(settings, profit, eligible)
    social, personal = _rate(settings, "social_contribution_rate"), _rate(settings, "personal_tax_rate")
    municipal, costs = _rate(settings, "municipal_tax_rate"), _rate(settings, "professional_costs_rate")
    withholding, reserve_tax = _rate(settings, "dividend_withholding_rate"), _rate(settings, "reserve_tax_rate")
    wait = settings["reserve_wait_years"]
    taxable = (1 - social) * (1 - costs)
    salary_k = 1 - social - taxable * personal * (1 + municipal)
    reserve_base = (1 - c) / (1 + reserve_tax)
    rows = [
        _row("salary", salary_k),
        _row("dividend", (1 - c) * (1 - withholding)),
        # Interest on what the director lent the company is a cost, so it uses no corporate tax: only the withholding.
        _row("current_account_interest", 1 - _rate(settings, "interest_withholding_rate")),
        _row("reserve_early", reserve_base * (1 - _rate(settings, "reserve_early_rate"))),
        _row("reserve", reserve_base * (1 - _rate(settings, "reserve_payout_rate"))),
    ]
    reserves = settings["liquidation_reserves"]
    total = sum(entry["amount"] for entry in reserves)
    existing = None
    if reserves and total > 0:
        weighted = sum(Decimal(entry["amount"]) * Decimal(str(entry["rate"])) for entry in reserves) / total / 100
        rows.append(_row("existing_reserves", reserve_base * (1 - weighted)))
        dates = sorted(entry["payable"] for entry in reserves)
        existing = {"gross": format_money(total), "earliest": dates[0], "latest": dates[-1]}
    interest = _rate(settings, "loan_interest_rate") * c * wait * NET
    rows.append(_row("loan_then_reserve", reserve_base * (1 - _rate(settings, "reserve_payout_rate")), interest))
    rows.append(_row("winding_up", reserve_base))
    threshold = None
    if settings["tax_reduced_threshold"] > 0:
        band = max(0, min(profit, settings["tax_reduced_band"]))
        saving = (_rate(settings, "tax_standard_rate") - _rate(settings, "tax_reduced_rate")) * band
        threshold = {
            "salary": format_money(settings["tax_reduced_threshold"]),
            "saving": format_money(int((saving).to_integral_value())),
        }
    return {
        "corporate_rate": float(round(c * 100, 2)), "wait_years": wait, "routes": rows,
        "credit_interest_rate": settings["credit_interest_rate"],
        "existing_reserves": existing, "threshold": threshold,
    }


def payout_routes(database_path: Path, today: Optional[date] = None) -> dict[str, Any]:
    """The routes for next year's corporate tax estimate (profit and whether it gets the reduced rate)."""
    year = corporate_tax(database_path, today)["years"][1]
    with connect(database_path) as db:
        settings = load_settings(db)
    return {"year": year["year"], "profit": year["profit"], **compute_routes(settings, parse_money(year["profit"]), year["eligible"])}
