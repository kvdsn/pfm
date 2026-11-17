"""Planned items: one-off expected income or expenses, such as a tax payment, that the forecast includes."""

import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional, Union

from .database import UNCATEGORIZED, connect, format_money, require_account_type
from .forecast_settings import ROLES, load_settings
from .recurring import _check_category, _now, _parse_date, _signed_cents
from .reports import COUNTED_SQL
from .rules import transaction_texts

# A transaction can be suggested as the payment of a planned item when its amount is within this share of the
# planned amount (an exact match always qualifies) and it was booked from MATCH_DAYS_BEFORE days before to
# MATCH_DAYS_AFTER days after the planned date.
MATCH_TOLERANCE_PERCENT = 10
MATCH_DAYS_BEFORE = 30
MATCH_DAYS_AFTER = 45
# An unbooked item dated up to this many days ago still counts in the forecast, at today's date; older ones drop out.
OVERDUE_DAYS_COUNTED = 60

SELECT = """
    SELECT p.*, c.name AS category,
           o.account_type AS other_account_type, o.date AS other_date,
           o.amount_cents AS other_amount_cents, o.note AS other_note
    FROM planned_items p
    LEFT JOIN categories c ON c.id = p.category_id
    LEFT JOIN planned_items o ON o.id = p.linked_item_id
"""


def _booked_view(db: sqlite3.Connection, transaction_id: int) -> Optional[dict[str, Any]]:
    row = db.execute(
        """
        SELECT t.id, t.booking_date, t.amount_cents, t.merchant, t.description, t.reference, t.source_json, a.bank
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE t.id = ? AND t.deleted_at IS NULL
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "transaction_id": row["id"], "date": row["booking_date"], "amount": format_money(row["amount_cents"]),
        "merchant": transaction_texts(dict(row), row["bank"])["merchant"],
    }


def _view(
    db: sqlite3.Connection, row: sqlite3.Row, today: date, suggestions: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """An item with its status: `booked` (a confirmed live transaction paid it, shown under `booked`), `overdue`
    (unbooked and dated before today) or `upcoming`; an unbooked item also carries the `suggestion`, if any."""
    linked = None if row["linked_item_id"] is None or row["other_account_type"] is None else {
        "id": row["linked_item_id"], "account_type": row["other_account_type"], "date": row["other_date"],
        "amount": format_money(row["other_amount_cents"]), "note": row["other_note"],
    }
    booked = None if row["transaction_id"] is None else _booked_view(db, row["transaction_id"])
    if booked is not None:
        status = "booked"
    else:
        status = "overdue" if row["date"] < today.isoformat() else "upcoming"
    return {
        "id": row["id"], "account_type": row["account_type"], "date": row["date"],
        "amount": format_money(row["amount_cents"]), "category_id": row["category_id"],
        "category": row["category"], "note": row["note"], "linked": linked,
        "dividend_group": row["dividend_group"], "paid_by_dividend": row["dividend_group"] is not None,
        "status": status, "booked": booked, "suggestion": None if booked else suggestions.get(row["id"]),
    }


def _suggestions(db: sqlite3.Connection, account_type: str) -> dict[int, dict[str, Any]]:
    """The best matching transaction for each unbooked item of the entity, by item id.

    Candidates are the entity's live transactions that the app counts (COUNTED_SQL: not a card settlement that
    its statement's purchases replace, not a transfer between the entity's own accounts, so a payment is never
    matched twice), not booking another item, not dismissed for the item, of the planned amount's sign, within
    the tolerance and the date window, and in the item's category, in Uncategorized, or any category when the
    item has none. The best is the one in the item's own category, then the closest amount, then the closest
    date. Items are taken in date order and each transaction goes to one item only."""
    items = db.execute(
        """
        SELECT p.id, p.date, p.amount_cents, p.category_id FROM planned_items p
        WHERE p.account_type = ?
          AND NOT EXISTS (SELECT 1 FROM transactions b WHERE b.id = p.transaction_id AND b.deleted_at IS NULL)
        ORDER BY p.date, p.id
        """,
        (account_type,),
    ).fetchall()
    if not items:
        return {}
    first = date.fromisoformat(items[0]["date"]) - timedelta(days=MATCH_DAYS_BEFORE)
    last = date.fromisoformat(items[-1]["date"]) + timedelta(days=MATCH_DAYS_AFTER)
    rows = db.execute(
        f"""
        SELECT t.id, t.booking_date, t.amount_cents, t.category_id, c.name AS category,
               t.merchant, t.description, t.reference, t.source_json, a.bank
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE a.account_type = ? AND t.deleted_at IS NULL AND {COUNTED_SQL}
          AND t.booking_date BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM planned_items b WHERE b.transaction_id = t.id)
        ORDER BY t.booking_date, t.id
        """,
        (account_type, first.isoformat(), last.isoformat()),
    ).fetchall()
    dismissed = {
        (row[0], row[1]) for row in db.execute(
            "SELECT d.planned_item_id, d.transaction_id FROM planned_match_dismissals d "
            "JOIN planned_items p ON p.id = d.planned_item_id WHERE p.account_type = ?", (account_type,)
        )
    }
    found: dict[int, dict[str, Any]] = {}
    taken: set[int] = set()
    for item in items:
        planned, when = item["amount_cents"], date.fromisoformat(item["date"])
        best = None
        for row in rows:
            cents = row["amount_cents"]
            if row["id"] in taken or (item["id"], row["id"]) in dismissed or (cents > 0) != (planned > 0):
                continue
            gap = abs(cents - planned)
            distance = abs((date.fromisoformat(row["booking_date"]) - when).days)
            if gap * 100 > abs(planned) * MATCH_TOLERANCE_PERCENT:
                continue
            if not -MATCH_DAYS_BEFORE <= (date.fromisoformat(row["booking_date"]) - when).days <= MATCH_DAYS_AFTER:
                continue
            same = item["category_id"] is not None and row["category_id"] == item["category_id"]
            if not (same or item["category_id"] is None or row["category_id"] is None or row["category"] == UNCATEGORIZED):
                continue
            rank = (0 if same else 1, gap, distance, row["id"])
            if best is None or rank < best[0]:
                best = (rank, row)
        if best is not None:
            row = best[1]
            taken.add(row["id"])
            found[item["id"]] = {
                "transaction_id": row["id"], "date": row["booking_date"], "amount": format_money(row["amount_cents"]),
                "merchant": transaction_texts(dict(row), row["bank"])["merchant"], "category": row["category"],
            }
    return found


def _find(db: sqlite3.Connection, account_type: str, item_id: int, today: Optional[date] = None) -> dict[str, Any]:
    row = db.execute(SELECT + " WHERE p.id = ? AND p.account_type = ?", (item_id, account_type)).fetchone()
    if row is None:
        raise LookupError("Planned item not found")
    return _view(db, row, today or date.today(), _suggestions(db, account_type))


def list_planned(database_path: Path, account_type: str, today: Optional[date] = None) -> list[dict[str, Any]]:
    """The entity's planned items: those not yet booked first (overdue and upcoming), then the booked ones, each
    group with the earliest date first."""
    require_account_type(account_type)
    with connect(database_path) as db:
        rows = db.execute(SELECT + " WHERE p.account_type = ? ORDER BY p.date, p.id", (account_type,)).fetchall()
        suggestions = _suggestions(db, account_type)
        views = [_view(db, row, today or date.today(), suggestions) for row in rows]
    return sorted(views, key=lambda view: view["status"] == "booked")  # stable: dates stay in order


def _check_booking(db: sqlite3.Connection, account_type: str, item: dict[str, Any], transaction_id: int) -> None:
    row = db.execute(
        "SELECT t.amount_cents, t.deleted_at, a.account_type FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.id = ?", (transaction_id,),
    ).fetchone()
    if row is None or row["deleted_at"] is not None or row["account_type"] != account_type:
        raise LookupError("Transaction not found")
    other = db.execute(
        "SELECT 1 FROM planned_items WHERE transaction_id = ? AND id != ?", (transaction_id, item["id"])
    ).fetchone()
    if other is not None:
        raise ValueError("That transaction already books another planned item")
    if (row["amount_cents"] > 0) != (Decimal(item["amount"]) > 0):
        raise ValueError("The transaction and the planned item must both be income or both be expenses")


def book_planned(database_path: Path, account_type: str, item_id: int, transaction_id: int) -> dict[str, Any]:
    """Book the item against the transaction that paid it. Only the owner's confirmation does this; the amount
    and date are not checked again."""
    require_account_type(account_type)
    with connect(database_path) as db:
        item = _find(db, account_type, item_id)
        _check_booking(db, account_type, item, transaction_id)
        db.execute(
            "UPDATE planned_items SET transaction_id = ?, updated_at = ? WHERE id = ?", (transaction_id, _now(), item_id)
        )
        db.execute("DELETE FROM planned_match_dismissals WHERE planned_item_id = ?", (item_id,))
        return _find(db, account_type, item_id)


def unbook_planned(database_path: Path, account_type: str, item_id: int) -> dict[str, Any]:
    require_account_type(account_type)
    with connect(database_path) as db:
        _find(db, account_type, item_id)
        db.execute("UPDATE planned_items SET transaction_id = NULL, updated_at = ? WHERE id = ?", (_now(), item_id))
        return _find(db, account_type, item_id)


def dismiss_match(database_path: Path, account_type: str, item_id: int, transaction_id: int) -> dict[str, Any]:
    """"Not this one": never suggest the transaction for the item again, so the next candidate shows."""
    require_account_type(account_type)
    with connect(database_path) as db:
        _find(db, account_type, item_id)
        if db.execute(
            "SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ? AND a.account_type = ?",
            (transaction_id, account_type),
        ).fetchone() is None:
            raise LookupError("Transaction not found")
        db.execute(
            "INSERT OR IGNORE INTO planned_match_dismissals(planned_item_id, transaction_id) VALUES (?, ?)",
            (item_id, transaction_id),
        )
        return _find(db, account_type, item_id)


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
        db.execute(f"DELETE FROM planned_match_dismissals WHERE planned_item_id IN ({', '.join('?' for _ in ids)})", ids)
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


def load_planned(db: sqlite3.Connection, account_type: str, today: date, end: date) -> list[dict[str, Any]]:
    """The items the forecast counts, in cents, oldest first: those not booked by a live transaction, dated from
    OVERDUE_DAYS_COUNTED days before `today` to `end`. A date already past moves to today, like a reimbursement
    claim's; items older than that drop out."""
    rows = db.execute(
        """
        SELECT * FROM planned_items p
        WHERE account_type = ? AND date BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM transactions b WHERE b.id = p.transaction_id AND b.deleted_at IS NULL)
        ORDER BY date, id
        """,
        (account_type, (today - timedelta(days=OVERDUE_DAYS_COUNTED)).isoformat(), end.isoformat()),
    )
    return [
        {"date": max(date.fromisoformat(row["date"]), today), "cents": row["amount_cents"],
         "category_id": row["category_id"], "note": row["note"]}
        for row in rows
    ]
