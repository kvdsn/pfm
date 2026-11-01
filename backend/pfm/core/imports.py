"""Accounts, staged CSV imports, and import history."""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .database import (
    ACCOUNT_TYPES,
    UNCATEGORIZED,
    connect,
    default_flow_type,
    format_money,
    get_category_id,
    masked_iban,
    require_account_type,
)
from .parsing import parse_export, pending_fingerprints, transaction_fingerprint
from .rules import account_rules, classify, matching_rule, rule_values
from .source_files import move_account_folder, save_source_file
from .statements import refresh_card_settlements, remove_empty_statements, store_statements

STAGING_TTL_SECONDS = 30 * 60
PREVIEW_ROWS = 10


def account_label(bank: str, kind: str) -> str:
    """How the interface names an account's source, such as "KBC" or "Belfius card"."""
    return f"{bank} card" if kind == "card" else bank


def accounts_list(database_path: Path) -> list[dict[str, Any]]:
    with connect(database_path) as db:
        rows = db.execute("SELECT id, iban, account_type, bank, kind FROM accounts ORDER BY account_type, id").fetchall()
    return [
        {
            "id": row["id"],
            "masked_iban": masked_iban(row["iban"]),
            "account_type": row["account_type"],
            "label": account_label(row["bank"], row["kind"]),
        }
        for row in rows
    ]


def account_overview(database_path: Path) -> list[dict[str, Any]]:
    """Every account of every entity with the period its active transactions cover, for the accounts view, each with its imports, newest first."""
    with connect(database_path) as db:
        accounts = db.execute("SELECT id, iban, account_type, bank, kind FROM accounts").fetchall()
        spans = {
            row["account_id"]: row
            for row in db.execute(
                """
                SELECT account_id, COUNT(*) AS transaction_count, MIN(booking_date) AS first_date,
                       MAX(booking_date) AS last_date
                FROM transactions WHERE deleted_at IS NULL GROUP BY account_id
                """
            )
        }
        # The newest balance is the latest balance date, and within a day the first row of the export.
        balances = {
            row["account_id"]: row["balance_cents"]
            for row in db.execute(
                """
                SELECT account_id, balance_cents FROM (
                    SELECT account_id, balance_cents,
                           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY balance_date DESC, row_number ASC) AS rank
                    FROM transactions WHERE deleted_at IS NULL AND balance_cents IS NOT NULL
                ) WHERE rank = 1
                """
            )
        }
        last_imports = {
            row["account_id"]: row["imported_at"]
            for row in db.execute("SELECT account_id, MAX(imported_at) AS imported_at FROM imports GROUP BY account_id")
        }
        imports: dict[int, list[dict[str, Any]]] = {}
        for record in _import_records(db, "1 = 1", ()):
            imports.setdefault(record["account_id"], []).append(record)
    overview = [
        {
            "id": account["id"],
            "account_type": account["account_type"],
            "bank": account["bank"],
            "kind": account["kind"],
            "label": account_label(account["bank"], account["kind"]),
            "masked": masked_iban(account["iban"]),
            "transaction_count": spans[account["id"]]["transaction_count"] if account["id"] in spans else 0,
            "first_date": spans[account["id"]]["first_date"] if account["id"] in spans else None,
            "last_date": spans[account["id"]]["last_date"] if account["id"] in spans else None,
            "latest_balance": format_money(balances.get(account["id"])),
            "last_import_at": last_imports.get(account["id"]),
            "imports": imports.get(account["id"], []),
        }
        for account in accounts
    ]
    overview.sort(
        key=lambda item: (
            ACCOUNT_TYPES.index(item["account_type"]), item["bank"], item["kind"] != "bank", item["label"], item["id"],
        )
    )
    return overview


def _find_account(db: sqlite3.Connection, iban: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT id, iban, bank, kind, account_type FROM accounts WHERE iban = ?", (iban,)).fetchone()


def _remap_account(db: sqlite3.Connection, account_id: int, account_type: str) -> None:
    """Move an account to the other account type, keeping category names."""
    for transaction in db.execute(
        """
        SELECT t.id, c.name AS category
        FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE t.account_id = ?
        """,
        (account_id,),
    ).fetchall():
        db.execute(
            "UPDATE transactions SET category_id = ? WHERE id = ?",
            (get_category_id(db, account_type, transaction["category"]), transaction["id"]),
        )
    db.execute("UPDATE accounts SET account_type = ? WHERE id = ?", (account_type, account_id))


def _move_source_folder(database_path: Path, account: Any, account_type: str) -> None:
    """Best effort: a source-data folder that cannot be moved never undoes the remap."""
    try:
        move_account_folder(
            database_path, account["bank"], account["kind"], account["iban"], account["account_type"], account_type
        )
    except OSError:
        pass


def remap_account(database_path: Path, account_id: int, account_type: str) -> None:
    require_account_type(account_type)
    with connect(database_path) as db:
        account = db.execute(
            "SELECT iban, bank, kind, account_type FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise LookupError("Account not found")
        if account["account_type"] != account_type:
            _remap_account(db, account_id, account_type)
    if account["account_type"] != account_type:
        _move_source_folder(database_path, account, account_type)


def _known_fingerprints(db: sqlite3.Connection, account: Any) -> dict[str, Optional[int]]:
    """The account's stored fingerprints, with Belfius rows also under their pending fingerprint, each with the
    row's balance."""
    known: dict[str, Optional[int]] = {}
    if account is None:
        return known
    by_import: dict[int, list[sqlite3.Row]] = {}
    for row in db.execute(
        "SELECT import_id, fingerprint, source_json, balance_cents FROM transactions "
        "WHERE account_id = ? ORDER BY import_id, row_number",
        (account["id"],),
    ):
        known[row["fingerprint"]] = row["balance_cents"]
        by_import.setdefault(row["import_id"], []).append(row)
    for stored in by_import.values():
        for row, pending in zip(stored, pending_fingerprints([json.loads(row["source_json"]) for row in stored])):
            if pending is not None:
                known.setdefault(pending, row["balance_cents"])
    return known


def _is_known(fingerprint: str, pending: Optional[str], known: Any) -> bool:
    return fingerprint in known or (pending is not None and pending in known)


def fill_chained_balances(
    db: sqlite3.Connection, rows: list[dict[str, str]], account: Any, known: dict[str, Optional[int]]
) -> None:
    """Reconstruct missing Belfius balances: back from a newer import, or else forward from the balances of the
    rows this export repeats, starting from the last balance before it."""
    if not rows or rows[0].get("_chain_anchor") != "1":
        return
    for row in rows:
        row.pop("_chain_anchor", None)
    if account is None:
        return
    oldest_first = sorted(
        zip(rows, pending_fingerprints(rows)), key=lambda pair: int(pair[0]["_row_number"]), reverse=True
    )
    newest_date = max(row["_booking_date"] for row in rows)
    anchor = db.execute(
        """
        SELECT amount_cents, balance_cents FROM transactions
        WHERE account_id = ? AND deleted_at IS NULL
          AND balance_cents IS NOT NULL AND booking_date > ?
        ORDER BY booking_date ASC, id DESC
        LIMIT 1
        """,
        (int(account["id"]), newest_date),
    ).fetchone()
    if anchor is not None:
        balance = int(anchor["balance_cents"]) - int(anchor["amount_cents"])
        for row, _ in reversed(oldest_first):
            row["_balance_cents"] = str(balance)
            balance -= int(row["_amount_cents"])
        return
    before = db.execute(
        """
        SELECT balance_cents FROM transactions
        WHERE account_id = ? AND deleted_at IS NULL
          AND balance_cents IS NOT NULL AND booking_date < ?
        ORDER BY booking_date DESC, import_id DESC, row_number ASC
        LIMIT 1
        """,
        (int(account["id"]), min(row["_booking_date"] for row in rows)),
    ).fetchone()
    running = None if before is None else int(before["balance_cents"])
    for row, pending in oldest_first:
        fingerprint = transaction_fingerprint(row)
        stored = known.get(fingerprint) if fingerprint in known else known.get(pending) if pending else None
        if stored is not None:
            running = stored
        elif running is not None:
            running += int(row["_amount_cents"])
        if running is not None:
            row["_balance_cents"] = str(running)


def _import_records(db: sqlite3.Connection, where: str, params: tuple, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Imports matching `where` (over the `a` accounts alias), newest first, with their active transaction counts."""
    rows = db.execute(
        f"""
        SELECT i.id, i.account_id, i.filename, i.imported_at, i.row_count, i.inserted_count,
               i.duplicate_count, a.iban, a.bank, a.kind, COUNT(t.id) AS transaction_count,
               COALESCE(SUM(t.note != '' OR t.follow_up IS NOT NULL), 0) AS annotated
        FROM imports i
        JOIN accounts a ON a.id = i.account_id
        LEFT JOIN transactions t ON t.import_id = i.id AND t.deleted_at IS NULL
        WHERE {where}
        GROUP BY i.id
        ORDER BY i.imported_at DESC, i.id DESC
        LIMIT ?
        """,
        (*params, -1 if limit is None else limit),
    ).fetchall()
    return [
        {
            **{key: row[key] for key in (
                "id", "account_id", "filename", "imported_at", "row_count", "inserted_count",
                "duplicate_count", "transaction_count", "annotated",
            )},
            "masked_iban": masked_iban(row["iban"]),
            "account_label": account_label(row["bank"], row["kind"]),
        }
        for row in rows
    ]


def import_history(database_path: Path, account_type: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """An account type's imports, newest first; all of them unless `limit` is given."""
    with connect(database_path) as db:
        return _import_records(db, "a.account_type = ?", (account_type,), limit)


def undo_import(database_path: Path, import_id: int) -> dict[str, Any]:
    """Remove an import's transactions; account mappings and categories are kept."""
    with connect(database_path) as db:
        imported = db.execute("SELECT filename FROM imports WHERE id = ?", (import_id,)).fetchone()
        if imported is None:
            raise LookupError("Import not found")
        db.execute(
            """
            UPDATE transactions SET replaced_by_statement_id = NULL
            WHERE replaced_by_statement_id IN (
                SELECT statement_id FROM transactions WHERE import_id = ? AND statement_id IS NOT NULL
            )
            """,
            (import_id,),
        )
        # Clear the claims' links to these transactions first, so no claim keeps pointing at a deleted one.
        for column in ("source_transaction_id", "paid_transaction_id"):
            db.execute(
                f"UPDATE reimbursement_claims SET {column} = NULL "
                f"WHERE {column} IN (SELECT id FROM transactions WHERE import_id = ?)",
                (import_id,),
            )
        deleted = db.execute("DELETE FROM transactions WHERE import_id = ?", (import_id,))
        db.execute("DELETE FROM imports WHERE id = ?", (import_id,))
        remove_empty_statements(db)
        refresh_card_settlements(db)
    return {"filename": imported["filename"], "removed_transactions": deleted.rowcount}


def _expire_staged_imports(staging: dict[str, Any], now: float) -> None:
    for token, staged in list(staging.items()):
        if now - staged["created_at"] > STAGING_TTL_SECONDS:
            staging.pop(token, None)


def stage_import(
    database_path: Path, filename: str, contents: bytes, staging: dict[str, Any]
) -> dict[str, Any]:
    """Parse an export and keep it staged for review; nothing is stored yet."""
    parsed = parse_export(contents)
    iban, rows, unknown_columns = parsed.account, parsed.rows, parsed.unknown_columns
    now = datetime.now().timestamp()
    _expire_staged_imports(staging, now)
    with connect(database_path) as db:
        account = _find_account(db, iban)
        known = _known_fingerprints(db, account)
        fill_chained_balances(db, rows, account, known)
        rules = {account_type: account_rules(db, account_type) for account_type in ACCOUNT_TYPES}

    duplicates = 0
    existing = set(known)
    for row, pending in zip(rows, pending_fingerprints(rows)):
        fingerprint = transaction_fingerprint(row)
        if _is_known(fingerprint, pending, existing):
            duplicates += 1
        existing.update(item for item in (fingerprint, pending) if item)

    preview = []
    for row in rows[:PREVIEW_ROWS]:
        cents = int(row["_amount_cents"])
        suggestions = {}
        for account_type, rules_for_type in rules.items():
            category, flow_type = classify(rules_for_type, cents, row)
            suggestions[account_type] = {"category": category, "flow_type": flow_type}
        preview.append(
            {"date": row["_booking_date"], **rule_values(row), "amount": format_money(cents), "suggestions": suggestions}
        )

    token = uuid.uuid4().hex
    safe_filename = Path(filename.replace("\\", "/")).name[:255] or "bank-export.csv"
    safe_filename = safe_filename.replace(iban, masked_iban(iban))
    staging[token] = {
        "parsed": parsed, "iban": iban, "rows": rows, "filename": safe_filename, "contents": contents, "created_at": now,
    }
    account_type = account["account_type"] if account else None
    return {
        "token": token,
        "filename": safe_filename,
        "masked_iban": masked_iban(iban),
        "account_label": account_label(parsed.bank, parsed.kind),
        "account_type": account_type,
        "account_type_required": account_type is None,
        "transaction_count": len(rows),
        "duplicate_count": duplicates,
        "new_count": len(rows) - duplicates,
        "unknown_columns": unknown_columns,
        "preview": preview,
        "sample_truncated": len(rows) > PREVIEW_ROWS,
    }


def cancel_staged_import(token: str, staging: dict[str, Any]) -> None:
    staging.pop(token, None)


def commit_import(
    database_path: Path, token: str, account_type: str, staging: dict[str, Any]
) -> dict[str, Any]:
    """Store a staged import, mapping its account to the chosen type and skipping duplicates."""
    staged = staging.get(token)
    if staged is None or datetime.now().timestamp() - staged["created_at"] > STAGING_TTL_SECONDS:
        staging.pop(token, None)
        raise LookupError("Import preview expired; please select the file again")
    require_account_type(account_type)

    inserted = 0
    duplicates = 0
    now = datetime.now().astimezone().isoformat()
    with connect(database_path) as db:
        parsed = staged["parsed"]
        account = _find_account(db, staged["iban"])
        if account is None:
            account_id = int(db.execute(
                "INSERT INTO accounts(iban, account_type, created_at, bank, kind) VALUES (?, ?, ?, ?, ?)",
                (staged["iban"], account_type, now, parsed.bank, parsed.kind),
            ).lastrowid)
        else:
            account_id = int(account["id"])
            if account["account_type"] != account_type:
                _remap_account(db, account_id, account_type)
        import_id = int(db.execute(
            """
            INSERT INTO imports(account_id, filename, imported_at, row_count, inserted_count, duplicate_count)
            VALUES (?, ?, ?, ?, 0, 0)
            """,
            (account_id, staged["filename"], now, len(staged["rows"])),
        ).lastrowid)
        statement_ids = store_statements(db, account_id, parsed.statements)
        rules = account_rules(db, account_type)
        existing = set(_known_fingerprints(db, account))
        for row, pending in zip(staged["rows"], pending_fingerprints(staged["rows"])):
            fingerprint = transaction_fingerprint(row)
            if _is_known(fingerprint, pending, existing):
                duplicates += 1
                continue
            existing.update(item for item in (fingerprint, pending) if item)
            cents = int(row["_amount_cents"])
            match = matching_rule(rules, row)
            if match:
                category_name, flow_type, source = match.category_name, match.flow_type, "rule"
            else:
                category_name, flow_type, source = UNCATEGORIZED, default_flow_type(cents), "default"
            try:
                db.execute(
                    """
                    INSERT INTO transactions(
                        account_id, import_id, row_number, booking_date, balance_date, value_date,
                        amount_cents, source_amount_cents, balance_cents, currency, merchant, description,
                        counterparty_iban, reference, category_id, flow_type, source_json, fingerprint,
                        statement_id, category_source, category_rule_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        import_id,
                        int(row["_row_number"]),
                        row["_booking_date"],
                        row["_booking_date"],
                        row.get("_value_date") or datetime.strptime(row["Valuta"], "%d/%m/%Y").date().isoformat(),
                        cents,
                        cents,
                        int(row["_balance_cents"]) if row["_balance_cents"] else None,
                        row["Munt"],
                        row.get("Naam tegenpartij", ""),
                        row.get("Vrije mededeling") or row.get("Omschrijving", ""),
                        row.get("rekeningnummer tegenpartij", "").replace(" ", "").upper(),
                        row.get("gestructureerde mededeling", ""),
                        get_category_id(db, account_type, category_name),
                        flow_type,
                        json.dumps({key: value for key, value in row.items() if not key.startswith("_")}, ensure_ascii=False),
                        fingerprint,
                        statement_ids.get(row.get("_statement", "")),
                        source,
                        match.id if match else None,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError as error:
                if "transactions.account_id, transactions.fingerprint" not in str(error):
                    raise
                duplicates += 1
        db.execute(
            "UPDATE imports SET inserted_count = ?, duplicate_count = ? WHERE id = ?",
            (inserted, duplicates, import_id),
        )
        refresh_card_settlements(db)
    staging.pop(token, None)
    if account is not None and account["account_type"] != account_type:
        _move_source_folder(database_path, account, account_type)
    result: dict[str, Any] = {"imported": inserted, "duplicates_skipped": duplicates, "account_type": account_type}
    # The import is already stored; a copy that cannot be saved is reported, never fatal.
    try:
        result["source_file"] = save_source_file(database_path, account_type, parsed, staged["contents"])
    except OSError as error:
        result["source_file_error"] = error.strerror or str(error)
    return result


def remove_transactions_before(database_path: Path, before: str) -> int:
    """Remove transactions booked before an ISO date from all reports and lists.

    Removed transactions are kept as hidden records, so re-importing the same export skips
    them as duplicates and card statements still count them toward the settlement they
    were paid with. Returns how many transactions were removed.
    """
    with connect(database_path) as db:
        removed = db.execute(
            "UPDATE transactions SET deleted_at = ? WHERE booking_date < ? AND deleted_at IS NULL",
            (datetime.now().astimezone().isoformat(), before),
        ).rowcount
        refresh_card_settlements(db)
    return removed
