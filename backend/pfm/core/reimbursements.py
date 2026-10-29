"""Reimbursement claims: what Business owes Personal for business costs paid privately.

A claim is open until it is linked to the Personal transaction that repaid it. The forecast expects every open
claim to be repaid as a linked pair of lines (see `forecast._reimbursement_lines`).
"""

import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

from .database import connect, format_money
from .forecast_settings import load_settings
from .recurring import _check_category, _now, _parse_date, _signed_cents
from .rules import transaction_texts

CANDIDATE_DAYS = 180  # how far back a repayment may have arrived
CLAIM_FIELDS = (
    "date", "description", "amount", "personal_category_id", "business_category_id", "expected_date",
    "paid_transaction_id", "note",
)

SELECT = """
    SELECT r.*, pc.name AS personal_category, bc.name AS business_category
    FROM reimbursement_claims r
    LEFT JOIN categories pc ON pc.id = r.personal_category_id
    LEFT JOIN categories bc ON bc.id = r.business_category_id
"""
TRANSACTION_SELECT = """
    SELECT t.id, t.booking_date, t.amount_cents, t.category_id, t.merchant, t.description, t.reference,
           t.source_json, a.bank, a.account_type
    FROM transactions t JOIN accounts a ON a.id = t.account_id
"""


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "date": row["booking_date"], "amount": format_money(row["amount_cents"]),
        "merchant": transaction_texts(dict(row), row["bank"])["merchant"],
    }


def _transaction(db: sqlite3.Connection, transaction_id: Any, label: str) -> sqlite3.Row:
    """A live Personal transaction; `label` names it in the error."""
    row = None if isinstance(transaction_id, bool) or not isinstance(transaction_id, int) else db.execute(
        TRANSACTION_SELECT + " WHERE t.id = ? AND t.deleted_at IS NULL", (transaction_id,)
    ).fetchone()
    if row is None or row["account_type"] != "personal":
        raise ValueError(f"{label} must be a personal transaction")
    return row


def _effective_date(row: sqlite3.Row, delay_days: int) -> date:
    if row["expected_date"]:
        return date.fromisoformat(row["expected_date"])
    return date.fromisoformat(row["date"]) + timedelta(days=delay_days)


def _view(db: sqlite3.Connection, row: sqlite3.Row, delay_days: int) -> dict[str, Any]:
    def linked(transaction_id: Optional[int]) -> Optional[dict[str, Any]]:
        found = None if transaction_id is None else db.execute(
            TRANSACTION_SELECT + " WHERE t.id = ?", (transaction_id,)
        ).fetchone()
        return None if found is None else _summary(found)

    return {
        "id": row["id"], "date": row["date"], "description": row["description"],
        "amount": format_money(row["amount_cents"]), "share": row["share"],
        "source_transaction_id": row["source_transaction_id"], "source": linked(row["source_transaction_id"]),
        "personal_category_id": row["personal_category_id"], "personal_category": row["personal_category"],
        "business_category_id": row["business_category_id"], "business_category": row["business_category"],
        "expected_date": row["expected_date"], "expected_on": _effective_date(row, delay_days).isoformat(),
        "paid_transaction_id": row["paid_transaction_id"], "paid": linked(row["paid_transaction_id"]),
        "note": row["note"],
    }


def _find(db: sqlite3.Connection, claim_id: int) -> sqlite3.Row:
    row = db.execute(SELECT + " WHERE r.id = ?", (claim_id,)).fetchone()
    if row is None:
        raise LookupError("Reimbursement claim not found")
    return row


def _candidates(db: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    """Personal incoming transactions from Business accounts, not a salary or transfer, not yet linked to a claim."""
    roles = load_settings(db)["roles"]
    excluded = [cid for role in ("salary_personal", "transfer_personal") if (cid := roles[role]) is not None]
    marks = ", ".join("?" for _ in excluded)
    rows = db.execute(
        TRANSACTION_SELECT + f"""
        WHERE a.account_type = 'personal' AND t.deleted_at IS NULL AND t.amount_cents > 0
          AND t.booking_date >= ? AND t.booking_date <= ?
          AND EXISTS (SELECT 1 FROM accounts b WHERE b.iban = t.counterparty_iban AND b.account_type = 'business')
          AND NOT EXISTS (SELECT 1 FROM reimbursement_claims r WHERE r.paid_transaction_id = t.id)
          {f'AND COALESCE(t.category_id, 0) NOT IN ({marks})' if excluded else ''}
        ORDER BY t.booking_date DESC, t.id DESC
        """,
        ((today - timedelta(days=CANDIDATE_DAYS)).isoformat(), today.isoformat(), *excluded),
    ).fetchall()
    return [_summary(row) for row in rows]


def list_claims(database_path: Path, today: Optional[date] = None) -> dict[str, Any]:
    """The claims newest first, what is still open, what was repaid this year and the possible repayments."""
    today = today or date.today()
    with connect(database_path) as db:
        delay = load_settings(db)["reimbursement_delay_days"]
        rows = db.execute(SELECT + " ORDER BY r.date DESC, r.id DESC").fetchall()
        open_total = sum(row["amount_cents"] for row in rows if row["paid_transaction_id"] is None)
        repaid = db.execute(
            """
            SELECT COALESCE(SUM(r.amount_cents), 0) FROM reimbursement_claims r
            JOIN transactions t ON t.id = r.paid_transaction_id WHERE substr(t.booking_date, 1, 4) = ?
            """,
            (str(today.year),),
        ).fetchone()[0]
        return {
            "claims": [_view(db, row, delay) for row in rows],
            "open_total": format_money(open_total), "paid_total_this_year": format_money(repaid),
            "candidates": _candidates(db, today),
        }


def _share_cents(cents: int, share: float) -> int:
    return int((Decimal(abs(cents)) * Decimal(str(share)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _positive_cents(amount: Any) -> int:
    cents = _signed_cents(amount)
    if cents <= 0:
        raise ValueError("Amount must be above 0")
    return cents


def _optional_date(value: Any, label: str) -> Optional[str]:
    return None if value in (None, "") else _parse_date(value, label).isoformat()


def add_claim(database_path: Path, fields: dict[str, Any]) -> dict[str, Any]:
    """Add a claim from a share of a Personal expense (`source_transaction_id`, `share`) or from `date`,
    `description` and `amount`. The expense supplies the date, description and Personal category unless given."""
    with connect(database_path) as db:
        source_id, share = fields.get("source_transaction_id"), fields.get("share")
        defaults: dict[str, Any] = {}
        if source_id is not None:
            if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 100:
                raise ValueError("Share must be above 0 and at most 100")
            source = _transaction(db, source_id, "The source")
            cents = _share_cents(source["amount_cents"], share)
            if cents <= 0:
                raise ValueError("Amount must be above 0")
            texts = transaction_texts(dict(source), source["bank"])
            defaults = {"date": source["booking_date"], "description": texts["merchant"] or "Expense",
                        "personal_category_id": source["category_id"]}
        else:
            share = None
            cents = _positive_cents(fields.get("amount"))
        when = _parse_date(fields.get("date") or defaults.get("date"), "Date").isoformat()
        description = (fields.get("description") or defaults.get("description") or "").strip()
        if not description:
            raise ValueError("Description is required")
        personal = fields.get("personal_category_id")
        personal = defaults.get("personal_category_id") if personal is None else personal
        business = fields.get("business_category_id")
        _check_category(db, "personal", personal)
        _check_category(db, "business", business)
        stamp = _now()
        cursor = db.execute(
            """
            INSERT INTO reimbursement_claims(date, description, amount_cents, share, source_transaction_id,
                personal_category_id, business_category_id, expected_date, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (when, description, cents, share, source_id, personal, business,
             _optional_date(fields.get("expected_date"), "Expected date"), (fields.get("note") or "").strip(), stamp, stamp),
        )
        return _view(db, _find(db, int(cursor.lastrowid)), load_settings(db)["reimbursement_delay_days"])


def update_claim(database_path: Path, claim_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Change any of date, description, amount, the categories, expected_date, note and paid_transaction_id
    (an id marks the claim paid, None reopens it). Changing the amount clears the share."""
    unknown = set(changes) - set(CLAIM_FIELDS)
    if unknown:
        raise ValueError("Unknown field: " + ", ".join(sorted(unknown)))
    with connect(database_path) as db:
        _find(db, claim_id)
        columns: dict[str, Any] = {}
        if "date" in changes:
            columns["date"] = _parse_date(changes["date"], "Date").isoformat()
        if "description" in changes:
            description = (changes["description"] or "").strip()
            if not description:
                raise ValueError("Description is required")
            columns["description"] = description
        if "amount" in changes:
            columns["amount_cents"] = _positive_cents(changes["amount"])
            columns["share"] = None
        for key, account_type in (("personal_category_id", "personal"), ("business_category_id", "business")):
            if key in changes:
                _check_category(db, account_type, changes[key])
                columns[key] = changes[key]
        if "expected_date" in changes:
            columns["expected_date"] = _optional_date(changes["expected_date"], "Expected date")
        if "paid_transaction_id" in changes:
            paid = changes["paid_transaction_id"]
            if paid is not None and _transaction(db, paid, "The repayment")["amount_cents"] <= 0:
                raise ValueError("The repayment must be an incoming transaction")
            columns["paid_transaction_id"] = paid
        if "note" in changes:
            columns["note"] = (changes["note"] or "").strip()
        if columns:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            db.execute(
                f"UPDATE reimbursement_claims SET {assignments}, updated_at = ? WHERE id = ?",
                (*columns.values(), _now(), claim_id),
            )
        return _view(db, _find(db, claim_id), load_settings(db)["reimbursement_delay_days"])


def delete_claim(database_path: Path, claim_id: int) -> None:
    with connect(database_path) as db:
        _find(db, claim_id)
        db.execute("DELETE FROM reimbursement_claims WHERE id = ?", (claim_id,))


def load_open_claims(db: sqlite3.Connection, delay_days: int) -> list[dict[str, Any]]:
    """The unpaid claims for the forecast: when each is expected to be repaid, in cents."""
    rows = db.execute("SELECT * FROM reimbursement_claims WHERE paid_transaction_id IS NULL ORDER BY date, id")
    return [
        {"expected": _effective_date(row, delay_days), "cents": row["amount_cents"],
         "personal_category_id": row["personal_category_id"], "business_category_id": row["business_category_id"],
         "description": row["description"]}
        for row in rows
    ]


def load_repayment_ids(db: sqlite3.Connection) -> list[int]:
    """The Personal transactions linked to a claim as its repayment."""
    return [row[0] for row in db.execute(
        "SELECT DISTINCT paid_transaction_id FROM reimbursement_claims WHERE paid_transaction_id IS NOT NULL"
    )]
