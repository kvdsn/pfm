"""Planned items: one-off expected income or expenses, such as a tax payment, that the forecast includes."""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional, Union

from .database import connect, format_money, require_account_type
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
        rows = db.execute(SELECT + " WHERE p.account_type = ? ORDER BY p.date DESC, p.id DESC", (account_type,))
        return [_view(row) for row in rows.fetchall()]


def add_planned(
    database_path: Path, account_type: str, day: str, amount: Union[str, int, float],
    category_id: Optional[int] = None, note: Optional[str] = None,
) -> dict[str, Any]:
    require_account_type(account_type)
    when = _parse_date(day, "Date").isoformat()
    cents = _signed_cents(amount)
    with connect(database_path) as db:
        _check_category(db, account_type, category_id)
        stamp = _now()
        cursor = db.execute(
            """
            INSERT INTO planned_items(account_type, date, amount_cents, category_id, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account_type, when, cents, category_id, (note or "").strip(), stamp, stamp),
        )
        return _find(db, account_type, int(cursor.lastrowid))


def update_planned(database_path: Path, account_type: str, item_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Change any of date, amount, category_id (None clears it) and note."""
    require_account_type(account_type)
    with connect(database_path) as db:
        _find(db, account_type, item_id)
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
        _find(db, account_type, item_id)
        _unlink(db, [item_id])
        db.execute("DELETE FROM planned_items WHERE id = ?", (item_id,))


def link_planned(
    database_path: Path, account_type: str, item_id: int, linked_item_id: Optional[int]
) -> dict[str, Any]:
    """Link an item to an item of another entity, both ways; None unlinks. Earlier links of either are cleared."""
    require_account_type(account_type)
    with connect(database_path) as db:
        _find(db, account_type, item_id)
        if linked_item_id is None:
            _unlink(db, [item_id])
            return _find(db, account_type, item_id)
        other = db.execute("SELECT account_type FROM planned_items WHERE id = ?", (linked_item_id,)).fetchone()
        if other is None:
            raise LookupError("Planned item to link not found")
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
