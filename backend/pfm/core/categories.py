"""Category management: listing, creating, renaming, removing, and merging categories."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import UNCATEGORIZED, connect, uncategorized_category_id
from .forecast_settings import remap_settings_category
from .rules import (
    Rule,
    account_rules,
    account_transactions,
    deciding_rules,
    first_match_counts,
    rule_health,
    rule_preview,
)


def category_list(database_path: Path, account_type: str) -> list[dict[str, Any]]:
    """Active categories with the flow type of each category's first rule, if any."""
    with connect(database_path) as db:
        rows = db.execute(
            """
            SELECT c.id, c.name, c.is_income,
                   (
                       SELECT r.flow_type FROM category_rules r
                       WHERE r.category_id = c.id ORDER BY r.priority, r.id LIMIT 1
                   ) AS rule_flow_type
            FROM categories c
            WHERE c.account_type = ?
            ORDER BY c.name COLLATE NOCASE
            """,
            (account_type,),
        ).fetchall()
    return [{**dict(row), "is_income": bool(row["is_income"])} for row in rows]


def category_management(database_path: Path, account_type: str) -> list[dict[str, Any]]:
    """Categories with their all-time transaction counts and their rules' match previews."""
    with connect(database_path) as db:
        categories = db.execute(
            """
            SELECT id, name, is_income FROM categories
            WHERE account_type = ? ORDER BY name COLLATE NOCASE
            """,
            (account_type,),
        ).fetchall()
        uncategorized_id = uncategorized_category_id(db, account_type)
        transaction_counts: dict[int, int] = {}
        for row in db.execute(
            """
            SELECT t.category_id, COUNT(*) AS transaction_count
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = ? AND t.deleted_at IS NULL
            GROUP BY t.category_id
            """,
            (account_type,),
        ):
            category_id = row["category_id"] if row["category_id"] is not None else uncategorized_id
            transaction_counts[category_id] = transaction_counts.get(category_id, 0) + row["transaction_count"]
        rule_rows = db.execute(
            """
            SELECT r.id, r.category_id, r.match_text, r.match_fields, r.flow_type, r.priority,
                   r.legacy_normalized, r.match_mode, c.name
            FROM category_rules r JOIN categories c ON c.id = r.category_id
            WHERE r.account_type = ? ORDER BY r.priority, r.id
            """,
            (account_type,),
        ).fetchall()
        transactions = account_transactions(db, account_type)
        rules = account_rules(db, account_type)
        deciders = deciding_rules(rules, transactions)
        first_matches = first_match_counts(deciders)
    order = {rule.id: index for index, rule in enumerate(rules)}

    rules_by_category: dict[int, list[dict[str, Any]]] = {}
    for row in rule_rows:
        rule = Rule.from_row(row)
        matched, preview = rule_preview(transactions, rule)
        rules_by_category.setdefault(row["category_id"], []).append(
            {
                "id": row["id"],
                "category_id": row["category_id"],
                "match_text": row["match_text"],
                "match_fields": json.loads(row["match_fields"]),
                "flow_type": row["flow_type"],
                "priority": row["priority"],
                "legacy_normalized": row["legacy_normalized"],
                "match_mode": row["match_mode"],
                **preview,
                "first_match_count": first_matches.get(row["id"], 0),
                **rule_health(rule, matched, deciders, order),
            }
        )
    return [
        {
            "id": category["id"],
            "name": category["name"],
            "is_income": bool(category["is_income"]),
            "transaction_count": transaction_counts.get(category["id"], 0),
            "rules": rules_by_category.get(category["id"], []),
        }
        for category in categories
    ]


def _normalized_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 80:
        raise ValueError("Category name must be between 1 and 80 characters")
    return normalized


def _editable_category(db: sqlite3.Connection, category_id: int) -> sqlite3.Row:
    category = db.execute(
        "SELECT id, account_type, name FROM categories WHERE id = ?",
        (category_id,),
    ).fetchone()
    if category is None:
        raise LookupError("Category not found")
    if category["name"] == UNCATEGORIZED:
        raise ValueError(f"{UNCATEGORIZED} is a protected system category")
    return category


def add_category(database_path: Path, account_type: str, name: str) -> dict[str, Any]:
    normalized = _normalized_name(name)
    with connect(database_path) as db:
        try:
            cursor = db.execute(
                "INSERT INTO categories(account_type, name) VALUES (?, ?)",
                (account_type, normalized),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("That category already exists") from error
        return {"id": cursor.lastrowid, "name": normalized}


def rename_category(database_path: Path, category_id: int, name: str) -> None:
    normalized = _normalized_name(name)
    with connect(database_path) as db:
        category = _editable_category(db, category_id)
        conflict = db.execute(
            "SELECT 1 FROM categories WHERE account_type = ? AND name = ? AND id != ?",
            (category["account_type"], normalized, category_id),
        ).fetchone()
        if conflict:
            raise ValueError("That category already exists")
        db.execute("UPDATE categories SET name = ? WHERE id = ?", (normalized, category_id))


def set_category_income(database_path: Path, category_id: int, is_income: bool) -> None:
    """Choose whether reports count the category as income (True) or as expense (False)."""
    with connect(database_path) as db:
        _editable_category(db, category_id)
        db.execute("UPDATE categories SET is_income = ? WHERE id = ?", (int(is_income), category_id))


def backfill_income_categories(db: sqlite3.Connection) -> None:
    """Set is_income on every category when the column is first added.

    A category becomes an income category when it has at least one rule and every rule's flow type
    is 'income', or when money coming in is at least 90% of the absolute volume of its counted
    transactions (not deleted, not between own accounts, not replaced by a card statement) and it has
    at least one such transaction. Everything else, and Uncategorized always, stays an expense category.
    """
    # Imported here because reports.py imports this package's database module.
    from .reports import COUNTED_SQL

    db.execute(
        f"""
        UPDATE categories SET is_income = 1
        WHERE name != ?
          AND (
              (
                  EXISTS (SELECT 1 FROM category_rules r WHERE r.category_id = categories.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM category_rules r
                      WHERE r.category_id = categories.id AND r.flow_type != 'income'
                  )
              )
              OR id IN (
                  SELECT t.category_id
                  FROM transactions t JOIN accounts a ON a.id = t.account_id
                  WHERE t.deleted_at IS NULL AND {COUNTED_SQL} AND t.category_id IS NOT NULL
                  GROUP BY t.category_id
                  HAVING COUNT(*) > 0
                     AND 10 * SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END)
                         >= 9 * SUM(ABS(t.amount_cents))
              )
          )
        """,
        (UNCATEGORIZED,),
    )


def remove_category(database_path: Path, category_id: int) -> None:
    """Move the category's transactions to Uncategorized, delete its rules and budgets, clear it from recurring and planned items, reimbursement claims and the forecast settings, and remove it.

    Transactions its deleted rules had categorized elsewhere move too, since nothing decides
    them any more.
    """
    with connect(database_path) as db:
        category = _editable_category(db, category_id)
        db.execute(
            """
            UPDATE transactions
            SET category_id = ?,
                flow_type = CASE WHEN amount_cents > 0 THEN 'income' ELSE 'expense' END,
                category_source = 'default', category_rule_id = NULL
            WHERE category_id = ?
               OR (category_source = 'rule'
                   AND category_rule_id IN (SELECT id FROM category_rules WHERE category_id = ?))
            """,
            (uncategorized_category_id(db, category["account_type"]), category_id, category_id),
        )
        db.execute("DELETE FROM category_rules WHERE category_id = ?", (category_id,))
        db.execute("DELETE FROM category_budgets WHERE category_id = ?", (category_id,))
        db.execute("UPDATE recurring_items SET category_id = NULL WHERE category_id = ?", (category_id,))
        db.execute("UPDATE planned_items SET category_id = NULL WHERE category_id = ?", (category_id,))
        for column in ("personal_category_id", "business_category_id"):
            db.execute(f"UPDATE reimbursement_claims SET {column} = NULL WHERE {column} = ?", (category_id,))
        remap_settings_category(db, category_id, None)
        db.execute("DELETE FROM categories WHERE id = ?", (category_id,))


def merge_categories(database_path: Path, account_type: str, source_id: int, target_id: int) -> None:
    """Move the source category's transactions, rules, budgets, recurring and planned items, reimbursement claims and forecast settings to the target, then remove the source."""
    if source_id == target_id:
        raise ValueError("Source and target categories must be different")
    with connect(database_path) as db:
        source = _editable_category(db, source_id)
        target = db.execute(
            "SELECT id, account_type FROM categories WHERE id = ?", (target_id,)
        ).fetchone()
        if target is None or source["account_type"] != account_type or target["account_type"] != account_type:
            raise LookupError("Source or target category not found")
        for table in ("transactions", "category_rules"):
            db.execute(f"UPDATE {table} SET category_id = ? WHERE category_id = ?", (target_id, source_id))
        # Budgets of the same year add up, so the merged category keeps the combined allowance.
        db.execute(
            """
            INSERT INTO category_budgets(category_id, year, amount_cents)
            SELECT ?, year, amount_cents FROM category_budgets WHERE category_id = ? AND true
            ON CONFLICT(category_id, year) DO UPDATE SET amount_cents = amount_cents + excluded.amount_cents
            """,
            (target_id, source_id),
        )
        db.execute("DELETE FROM category_budgets WHERE category_id = ?", (source_id,))
        db.execute("UPDATE recurring_items SET category_id = ? WHERE category_id = ?", (target_id, source_id))
        db.execute("UPDATE planned_items SET category_id = ? WHERE category_id = ?", (target_id, source_id))
        for column in ("personal_category_id", "business_category_id"):
            db.execute(f"UPDATE reimbursement_claims SET {column} = ? WHERE {column} = ?", (target_id, source_id))
        remap_settings_category(db, source_id, target_id)
        db.execute("DELETE FROM categories WHERE id = ?", (source_id,))
