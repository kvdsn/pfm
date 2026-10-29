"""Yearly category budgets: a spending limit for an expense category, a target for an income category."""

import re
from pathlib import Path
from typing import Any, Optional, Union

from .database import UNCATEGORIZED, connect, format_money
from .transactions import parse_amount_cents


def _valid_year(year: str) -> str:
    if len(year) != 4 or not year.isdigit():
        raise ValueError("Year must use YYYY format")
    return year


def _budget_cents(amount: Union[str, int, float, None]) -> Optional[int]:
    """Cents for a Belgian or plain amount ("1.500", "1500,50", "€ 2.000"); None when empty or zero."""
    if amount is None:
        return None
    text = re.sub(r"[\s€]", "", str(amount))
    if not text:
        return None
    cents = parse_amount_cents(text)
    if cents is None:
        raise ValueError(f"Invalid budget amount: {amount}")
    return cents or None


def list_budgets(database_path: Path, account_type: str, year: str) -> list[dict[str, Any]]:
    """The year's budgets of an entity's categories, in category order."""
    _valid_year(year)
    with connect(database_path) as db:
        rows = db.execute(
            """
            SELECT b.category_id, b.year, b.amount_cents
            FROM category_budgets b JOIN categories c ON c.id = b.category_id
            WHERE c.account_type = ? AND b.year = ?
            ORDER BY c.name COLLATE NOCASE
            """,
            (account_type, year),
        ).fetchall()
    return [
        {"category_id": row["category_id"], "year": row["year"], "amount": format_money(row["amount_cents"])}
        for row in rows
    ]


def set_budget(
    database_path: Path,
    account_type: str,
    category_id: int,
    year: str,
    amount: Union[str, int, float, None],
) -> dict[str, Any]:
    """Set or replace a category's budget for a year; an empty or zero amount removes it.

    Returns the stored budget, with amount None when there is none.
    """
    _valid_year(year)
    cents = _budget_cents(amount)
    with connect(database_path) as db:
        category = db.execute(
            "SELECT account_type, name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None or category["account_type"] != account_type:
            raise LookupError("Category not found")
        if category["name"] == UNCATEGORIZED:
            raise ValueError(f"{UNCATEGORIZED} cannot have a budget")
        if cents is None:
            db.execute("DELETE FROM category_budgets WHERE category_id = ? AND year = ?", (category_id, year))
        else:
            db.execute(
                """
                INSERT INTO category_budgets(category_id, year, amount_cents) VALUES (?, ?, ?)
                ON CONFLICT(category_id, year) DO UPDATE SET amount_cents = excluded.amount_cents
                """,
                (category_id, year, cents),
            )
    return {"category_id": category_id, "year": year, "amount": format_money(cents)}
