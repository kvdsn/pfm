"""Card statements and the bank settlements they replace."""

import sqlite3
from pathlib import Path
from typing import Any

from .card_exports import Statement
from .database import connect, format_money, masked_iban

SETTLEMENT_WINDOW_DAYS = 7


def store_statements(db: sqlite3.Connection, account_id: int, statements: list[Statement]) -> dict[str, int]:
    """Create or update an account's statements; returns their ids by statement key."""
    ids = {}
    for statement in statements:
        db.execute(
            """
            INSERT INTO card_statements(account_id, statement_key, debit_date) VALUES (?, ?, ?)
            ON CONFLICT(account_id, statement_key) DO UPDATE SET debit_date = excluded.debit_date
            """,
            (account_id, statement.key, statement.debit_date),
        )
        ids[statement.key] = int(db.execute(
            "SELECT id FROM card_statements WHERE account_id = ? AND statement_key = ?",
            (account_id, statement.key),
        ).fetchone()[0])
    return ids


def refresh_card_settlements(db: sqlite3.Connection) -> None:
    """Link every card statement to the bank transaction that paid it, rebuilding all links.

    A settlement matches when it is an active transaction of a bank account of the same
    account type, is debited within a week of the statement's debit date, and its amount
    equals what the statement left to pay. KBC settlements must also name the card's last
    four digits, since one bank account can pay several KBC cards.

    A statement whose refunds outweigh its purchases is not debited; its credit lowers the
    next statement's payment instead. Statements without a match keep counting their
    purchases, and the settlement, if any, keeps counting too.

    Removed purchases still count toward their statement's total: the settlement paid
    them, so it stays replaced by the purchases that remain.
    """
    db.execute("UPDATE transactions SET replaced_by_statement_id = NULL WHERE replaced_by_statement_id IS NOT NULL")
    statements = db.execute(
        """
        SELECT s.id, s.account_id, s.debit_date, a.account_type, a.bank, a.iban AS card,
               SUM(t.amount_cents) AS total_cents
        FROM card_statements s
        JOIN accounts a ON a.id = s.account_id
        JOIN transactions t ON t.statement_id = s.id
        GROUP BY s.id
        ORDER BY s.account_id, s.debit_date
        """
    ).fetchall()
    credit: dict[int, int] = {}
    for statement in statements:
        carried = credit.pop(statement["account_id"], 0)
        if statement["total_cents"] >= 0:
            credit[statement["account_id"]] = carried + statement["total_cents"]
            continue
        for amount in dict.fromkeys((statement["total_cents"] + carried, statement["total_cents"])):
            if amount < 0 and _link_settlement(db, statement, amount):
                break


def _link_settlement(db: sqlite3.Connection, statement: sqlite3.Row, amount_cents: int) -> bool:
    settlement = db.execute(
        """
        SELECT t.id FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.kind = 'bank' AND a.account_type = ? AND t.deleted_at IS NULL
          AND t.replaced_by_statement_id IS NULL AND t.amount_cents = ?
          AND abs(julianday(t.booking_date) - julianday(?)) <= ?
          AND upper(t.description) LIKE '%AFREKENING%'
          AND (? != 'KBC' OR replace(t.description, ' ', '') LIKE '%' || ? || '%')
        ORDER BY abs(julianday(t.booking_date) - julianday(?)), t.id
        LIMIT 1
        """,
        (
            statement["account_type"], amount_cents, statement["debit_date"], SETTLEMENT_WINDOW_DAYS,
            statement["bank"], statement["card"][-4:], statement["debit_date"],
        ),
    ).fetchone()
    if settlement:
        db.execute(
            "UPDATE transactions SET replaced_by_statement_id = ? WHERE id = ?",
            (statement["id"], settlement["id"]),
        )
    return settlement is not None


def remove_empty_statements(db: sqlite3.Connection) -> None:
    """Delete statements whose purchases were all removed, unlinking their settlements."""
    empty = "SELECT s.id FROM card_statements s WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.statement_id = s.id)"
    db.execute(f"UPDATE transactions SET replaced_by_statement_id = NULL WHERE replaced_by_statement_id IN ({empty})")
    db.execute(f"DELETE FROM card_statements WHERE id IN ({empty})")


def card_statements(database_path: Path, account_id: int) -> list[dict[str, Any]]:
    """A card's statements, newest first, with what each left to pay and how it was paid.

    `status` is `paid` (a bank settlement was replaced by the statement), `credit` (refunds
    outweighed purchases; the credit lowers the next payment), `nothing_due` (nothing left
    to pay, possibly thanks to an earlier credit) or `unpaid` (no matching settlement found).
    """
    with connect(database_path) as db:
        card_account(db, account_id)
        return list(reversed(statement_rows(db, account_id)))


def card_account(db: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    card = db.execute("SELECT id, bank, iban FROM accounts WHERE id = ? AND kind = 'card'", (account_id,)).fetchone()
    if card is None:
        raise LookupError("Card not found")
    return card


def statement_rows(db: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """A card's statements, oldest first, replaying the credit carried over as refresh_card_settlements does.

    Totals include removed purchases, because the settlement paid them.
    """
    rows = db.execute(
        """
        SELECT s.id, s.statement_key, s.debit_date, SUM(t.amount_cents) AS total_cents, MAX(t.import_id) AS import_id,
               SUM(t.deleted_at IS NULL) AS purchase_count, SUM(t.deleted_at IS NOT NULL) AS removed_count
        FROM card_statements s JOIN transactions t ON t.statement_id = s.id
        WHERE s.account_id = ?
        GROUP BY s.id
        ORDER BY s.debit_date
        """,
        (account_id,),
    ).fetchall()
    settlements = {
        row["replaced_by_statement_id"]: row
        for row in db.execute(
            """
            SELECT t.id, t.replaced_by_statement_id, t.booking_date, t.amount_cents, a.bank, a.iban
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            JOIN card_statements s ON s.id = t.replaced_by_statement_id
            WHERE s.account_id = ?
            """,
            (account_id,),
        )
    }
    result = []
    credit = 0
    for row in rows:
        carried, total = credit, row["total_cents"]
        settlement = settlements.get(row["id"])
        if total >= 0:
            credit = carried + total
            status, due = ("credit" if total > 0 else "nothing_due"), 0
        else:
            credit = 0
            if settlement:
                status, due = "paid", -settlement["amount_cents"]
            elif total + carried >= 0:
                status, due = "nothing_due", 0
            else:
                status, due = "unpaid", -(total + carried)
        result.append({
            "id": row["id"],
            "period": row["statement_key"],
            "debit_date": row["debit_date"],
            # The import that brought its purchases (the latest, should several have).
            "import_id": row["import_id"],
            "purchase_count": row["purchase_count"],
            "removed_count": row["removed_count"],
            "total": format_money(total),
            "carried_credit": format_money(carried) if carried else None,
            "due": format_money(due),
            "status": status,
            "settlement": {
                "id": settlement["id"],
                "booking_date": settlement["booking_date"],
                "amount": format_money(settlement["amount_cents"]),
                "account": f"{settlement['bank']} {masked_iban(settlement['iban'])}",
            } if settlement else None,
        })
    return result
