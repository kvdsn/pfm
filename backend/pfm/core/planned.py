"""Planned items: one-off expected income or expenses, such as a tax payment, that the forecast includes."""

import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional, Union

from .database import connect, format_money, require_account_type
from .forecast_settings import ROLES, load_settings
from .recurring import _check_category, _now, _parse_date, _signed_cents

SELECT = """
    SELECT p.*, c.name AS category,
           o.account_type AS other_account_type, o.date AS other_date,
           o.amount_cents AS other_amount_cents, o.note AS other_note
    FROM planned_items p
    LEFT JOIN categories c ON c.id = p.category_id
    LEFT JOIN planned_items o ON o.id = p.linked_item_id
"""


def _view(row: sqlite3.Row) -> dict[str, Any]:
    linked = None if row["linked_item_id"] is None or row["other_account_type"] is None else {
        "id": row["linked_item_id"], "account_type": row["other_account_type"], "date": row["other_date"],
        "amount": format_money(row["other_amount_cents"]), "note": row["other_note"],
    }
    return {
        "id": row["id"], "account_type": row["account_type"], "date": row["date"],
        "amount": format_money(row["amount_cents"]), "category_id": row["category_id"],
        "category": row["category"], "note": row["note"], "linked": linked,
        "dividend_group": row["dividend_group"], "paid_by_dividend": row["dividend_group"] is not None,
    }


def _find(db: sqlite3.Connection, account_type: str, item_id: int) -> dict[str, Any]:
    row = db.execute(SELECT + " WHERE p.id = ? AND p.account_type = ?", (item_id, account_type)).fetchone()
    if row is None:
        raise LookupError("Planned item not found")
    return _view(row)


def list_planned(database_path: Path, account_type: str) -> list[dict[str, Any]]:
    """The entity's planned items, newest date first."""
    require_account_type(account_type)
    with connect(database_path) as db:
        rows = db.execute(SELECT + " WHERE p.account_type = ? ORDER BY p.date, p.id", (account_type,))
        return [_view(row) for row in rows.fetchall()]


def _gross_cents(net: int, rate: Any) -> int:
    """What Business pays out for a dividend that leaves `net` after the withholding rate (in percent)."""
    return int((Decimal(net) / (1 - Decimal(str(rate)) / 100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _dividend_category(db: sqlite3.Connection, role: str) -> int:
    """The role's category, created when the default one does not exist yet (income in Personal, expense in Business)."""
    entity, name = ROLES[role]
    found = load_settings(db)["roles"][role]
    if found is not None:
        return found
    row = db.execute("SELECT id FROM categories WHERE account_type = ? AND name = ?", (entity, name)).fetchone()
    if row is not None:
        return int(row["id"])
    return int(db.execute(
        "INSERT INTO categories(account_type, name, is_income) VALUES (?, ?, ?)", (entity, name, int(entity == "personal"))
    ).lastrowid)


def _insert(
    db: sqlite3.Connection, account_type: str, when: str, cents: int, category_id: Optional[int], note: str,
    group: Optional[int] = None,
) -> int:
    stamp = _now()
    return int(db.execute(
        """
        INSERT INTO planned_items(account_type, date, amount_cents, category_id, note, dividend_group, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (account_type, when, cents, category_id, note, group, stamp, stamp),
    ).lastrowid)


def add_planned(
    database_path: Path, account_type: str, day: str, amount: Union[str, int, float],
    category_id: Optional[int] = None, note: Optional[str] = None, paid_by_dividend: bool = False,
) -> dict[str, Any]:
    """Add a planned item. In Personal, `paid_by_dividend` takes a net expense and also creates, on the same
    date, Personal's dividend income of that net amount and Business's dividend of the gross amount at the
    withholding rate now in force; the three share a `dividend_group` and the two dividends are linked. The
    purchase's view has the three lines under `created`."""
    require_account_type(account_type)
    when = _parse_date(day, "Date").isoformat()
    cents = _signed_cents(amount)
    if paid_by_dividend and (account_type != "personal" or cents >= 0):
        raise ValueError("Only an expense in Personal can be paid by dividend")
    text = (note or "").strip()
    with connect(database_path) as db:
        _check_category(db, account_type, category_id)
        if not paid_by_dividend:
            return _find(db, account_type, _insert(db, account_type, when, cents, category_id, text))
        gross = _gross_cents(-cents, load_settings(db)["dividend_withholding_rate"])
        purchase = _insert(db, "personal", when, cents, category_id, text)
        db.execute("UPDATE planned_items SET dividend_group = id WHERE id = ?", (purchase,))
        personal = _insert(db, "personal", when, -cents, _dividend_category(db, "dividend_personal"), text, purchase)
        business = _insert(db, "business", when, -gross, _dividend_category(db, "dividend_business"), text, purchase)
        db.execute("UPDATE planned_items SET linked_item_id = ? WHERE id = ?", (business, personal))
        db.execute("UPDATE planned_items SET linked_item_id = ? WHERE id = ?", (personal, business))
        view = _find(db, "personal", purchase)
        return {**view, "created": [view, _find(db, "personal", personal), _find(db, "business", business)]}


def _dividend_lines(db: sqlite3.Connection, group: int) -> dict[str, sqlite3.Row]:
    """The three lines of a dividend purchase: `purchase`, `personal` (its dividend income) and `business`."""
    rows = db.execute("SELECT * FROM planned_items WHERE dividend_group = ?", (group,)).fetchall()
    found = {"business": next(row for row in rows if row["account_type"] == "business")}
    personal = [row for row in rows if row["account_type"] == "personal"]
    found["personal"] = next(row for row in personal if row["linked_item_id"] is not None)
    found["purchase"] = next(row for row in personal if row["id"] != found["personal"]["id"])
    return found


def _update_dividend(
    db: sqlite3.Connection, account_type: str, item_id: int, group: int, columns: dict[str, Any]
) -> None:
    """Carry a change of date or amount of one of a dividend's three lines over to the other two. The amount
    sets the net: the purchase's and the Personal dividend's directly, the Business dividend's less the
    withholding. The other two lines are rewritten at the withholding rate now in force."""
    found = _dividend_lines(db, group)
    stamp = _now()
    if "date" in columns:
        db.execute(
            "UPDATE planned_items SET date = ?, updated_at = ? WHERE dividend_group = ?", (columns["date"], stamp, group)
        )
    if "amount_cents" not in columns:
        return
    cents = columns["amount_cents"]
    rate = load_settings(db)["dividend_withholding_rate"]
    if found["business"]["id"] == item_id:
        if cents >= 0:
            raise ValueError("A dividend paid out of Business must be an expense")
        net = int((Decimal(-cents) * (1 - Decimal(str(rate)) / 100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    else:
        net = -cents if found["purchase"]["id"] == item_id else cents
        if net <= 0:
            raise ValueError("The purchase must stay an expense, and its dividend income must stay income")
    gross = cents if found["business"]["id"] == item_id else -_gross_cents(net, rate)
    for key, value in (("purchase", -net), ("personal", net), ("business", gross)):
        db.execute(
            "UPDATE planned_items SET amount_cents = ?, updated_at = ? WHERE id = ?", (value, stamp, found[key]["id"])
        )


def update_planned(database_path: Path, account_type: str, item_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Change any of date, amount, category_id (None clears it) and note. For a purchase paid by dividend, the date
    and amount are carried over to its other two lines."""
    require_account_type(account_type)
    with connect(database_path) as db:
        group = _find(db, account_type, item_id)["dividend_group"]
        columns: dict[str, Any] = {}
        if "date" in changes:
            columns["date"] = _parse_date(changes["date"], "Date").isoformat()
        if "amount" in changes:
            columns["amount_cents"] = _signed_cents(changes["amount"])
        if "category_id" in changes:
            _check_category(db, account_type, changes["category_id"])
            columns["category_id"] = changes["category_id"]
        if "note" in changes:
            columns["note"] = (changes["note"] or "").strip()
        if group is not None:
            _update_dividend(db, account_type, item_id, group, columns)
            columns = {name: value for name, value in columns.items() if name not in ("date", "amount_cents")}
        if columns:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            db.execute(
                f"UPDATE planned_items SET {assignments}, updated_at = ? WHERE id = ?",
                (*columns.values(), _now(), item_id),
            )
        return _find(db, account_type, item_id)


def _unlink(db: sqlite3.Connection, item_ids: list[int]) -> None:
    """Clear the links of these items and of the items linked to them."""
    marks = ", ".join("?" for _ in item_ids)
    db.execute(
        f"UPDATE planned_items SET linked_item_id = NULL WHERE id IN ({marks}) OR linked_item_id IN ({marks})",
        (*item_ids, *item_ids),
    )


def delete_planned(database_path: Path, account_type: str, item_id: int) -> None:
    require_account_type(account_type)
    with connect(database_path) as db:
        group = _find(db, account_type, item_id)["dividend_group"]
        ids = [item_id] if group is None else [
            row["id"] for row in db.execute("SELECT id FROM planned_items WHERE dividend_group = ?", (group,))
        ]
        _unlink(db, ids)
        db.execute(f"DELETE FROM planned_items WHERE id IN ({', '.join('?' for _ in ids)})", ids)


def link_planned(
    database_path: Path, account_type: str, item_id: int, linked_item_id: Optional[int]
) -> dict[str, Any]:
    """Link an item to an item of another entity, both ways; None unlinks. Earlier links of either are cleared."""
    require_account_type(account_type)
    with connect(database_path) as db:
        if _find(db, account_type, item_id)["dividend_group"] is not None:
            raise ValueError("The lines of a purchase paid by dividend are linked by the app")
        if linked_item_id is None:
            _unlink(db, [item_id])
            return _find(db, account_type, item_id)
        other = db.execute("SELECT account_type FROM planned_items WHERE id = ?", (linked_item_id,)).fetchone()
        if other is None:
            raise LookupError("Planned item to link not found")
        if db.execute("SELECT dividend_group FROM planned_items WHERE id = ?", (linked_item_id,)).fetchone()[0] is not None:
            raise ValueError("The lines of a purchase paid by dividend are linked by the app")
        if other["account_type"] == account_type:
            raise ValueError("Link an item of another account type")
        _unlink(db, [item_id, linked_item_id])
        db.execute("UPDATE planned_items SET linked_item_id = ? WHERE id = ?", (linked_item_id, item_id))
        db.execute("UPDATE planned_items SET linked_item_id = ? WHERE id = ?", (item_id, linked_item_id))
        return _find(db, account_type, item_id)


def load_planned(db: sqlite3.Connection, account_type: str, start: date, end: date) -> list[dict[str, Any]]:
    """The entity's items dated from `start` to `end` inclusive, in cents, oldest first."""
    rows = db.execute(
        "SELECT * FROM planned_items WHERE account_type = ? AND date BETWEEN ? AND ? ORDER BY date, id",
        (account_type, start.isoformat(), end.isoformat()),
    )
    return [
        {"date": date.fromisoformat(row["date"]), "cents": row["amount_cents"],
         "category_id": row["category_id"], "note": row["note"]}
        for row in rows
    ]
