"""Categorization rules: matching, previews, and rule management.

Rules match case-insensitive text in a transaction's merchant, description, or memo: either
text the field contains, the text as a whole word, or the field's exact text.
Within an account type, the first matching rule in priority order wins.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from typing import Any, Optional

from .describe import summarize
from .revolut import counterparty_name
from .database import (
    ACCOUNT_TYPES,
    MATCH_MODES,
    UNCATEGORIZED,
    connect,
    default_flow_type,
    format_money,
    normalize_key,
    require_flow_type,
    uncategorized_category_id,
)

RULE_FIELDS = ("merchant", "description", "memo")


@lru_cache(maxsize=512)
def word_pattern(needle: str) -> "re.Pattern[str]":
    """Matches the (whitespace-collapsed, casefolded) text where no letter or digit touches it."""
    return re.compile(r"(?<![^\W_])" + re.escape(needle) + r"(?![^\W_])")


def transaction_texts(row: dict[str, Any], bank: str = "") -> dict[str, str]:
    """A transaction's original texts plus the short merchant name and detail line shown for it.

    Accepts a parsed CSV row or a stored transaction row (whose `source_json` holds the
    original columns).
    """
    source = row
    if isinstance(row.get("source_json"), str):
        source = {**row, **json.loads(row["source_json"])}
    # KBC's "Naam" column is the account holder, not the counterparty.
    counterparty = str(source.get("Naam tegenpartij") or "")
    description = str(source.get("Omschrijving") or source.get("description") or "")
    memo = str(
        source.get("Vrije mededeling")
        or source.get("Mededelingen")
        or source.get("gestructureerde mededeling")
        or source.get("reference")
        or ""
    )
    if "Started Date" in source:  # a Revolut row, staged or stored
        bank = bank or "Revolut"
        # Derived from the original texts, so stored rows follow improvements to the name cleanup.
        counterparty = counterparty_name(str(source.get("Type") or ""), str(source.get("Description") or ""))
    merchant, details = summarize(counterparty, description, memo, bank)
    return {
        "merchant": "" if merchant == "—" else merchant,
        "details": details,
        "description": description,
        "memo": memo,
        "counterparty": counterparty,
    }


def rule_values(row: dict[str, Any]) -> dict[str, str]:
    """Merchant, description, and memo as rules match them.

    The merchant is the counterparty's name, or the one the description mentions when the
    bank names no counterparty (such as the shop of a card payment).
    """
    texts = transaction_texts(row)
    return {field: texts[field] for field in RULE_FIELDS}


@dataclass(frozen=True)
class Rule:
    id: Optional[int]
    match_text: str
    match_fields: tuple[str, ...]
    legacy_normalized: bool = False
    category_id: Optional[int] = None
    category_name: str = ""
    flow_type: str = ""
    match_mode: str = "contains"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Rule":
        return cls(
            id=row["id"],
            match_text=row["match_text"],
            match_fields=tuple(json.loads(row["match_fields"])),
            legacy_normalized=bool(row["legacy_normalized"]),
            category_id=row["category_id"],
            category_name=row["name"],
            flow_type=row["flow_type"],
            match_mode=row["match_mode"],
        )

    def matches(self, values: dict[str, str]) -> bool:
        needle = self.match_text.casefold()
        if not needle:
            return False
        if self.match_mode == "exact":
            needle = " ".join(needle.split())
            return any(" ".join(values[field].casefold().split()) == needle for field in self.match_fields)
        if self.match_mode == "word":
            pattern = word_pattern(" ".join(needle.split()))
            return any(pattern.search(" ".join(values[field].casefold().split())) for field in self.match_fields)
        if self.legacy_normalized:
            return any(needle in normalize_key(values[field]).casefold() for field in self.match_fields)
        return any(needle in values[field].casefold() for field in self.match_fields)


def account_rules(db: sqlite3.Connection, account_type: str) -> list[Rule]:
    rows = db.execute(
        """
        SELECT r.id, r.category_id, r.match_text, r.match_fields, r.flow_type,
               r.legacy_normalized, r.match_mode, c.name
        FROM category_rules r JOIN categories c ON c.id = r.category_id
        WHERE r.account_type = ?
        ORDER BY r.priority, r.id
        """,
        (account_type,),
    ).fetchall()
    return [Rule.from_row(row) for row in rows]


def first_match(rules: list[Rule], values: dict[str, str]) -> Optional[Rule]:
    return next((rule for rule in rules if rule.matches(values)), None)


def matching_rule(rules: list[Rule], row: dict[str, Any]) -> Optional[Rule]:
    """The rule that decides a transaction row (a parsed CSV row or a stored one), or None."""
    return first_match(rules, rule_values(row))


def classify(rules: list[Rule], amount_cents: int, row: dict[str, Any]) -> tuple[str, str]:
    """Category name and flow type for a transaction under the given rules."""
    match = matching_rule(rules, row)
    if match:
        return match.category_name, match.flow_type
    return UNCATEGORIZED, default_flow_type(amount_cents)


def suggest_classification(
    db: sqlite3.Connection, account_type: str, amount_cents: int, row: dict[str, Any]
) -> tuple[str, str]:
    return classify(account_rules(db, account_type), amount_cents, row)


def account_transactions(
    db: sqlite3.Connection, account_type: str
) -> list[tuple[sqlite3.Row, dict[str, str]]]:
    """Active transactions of an account type, newest first, with their rule values."""
    rows = db.execute(
        """
        SELECT t.id, t.booking_date, t.merchant, t.description, t.reference,
               t.amount_cents, t.category_id, t.flow_type, t.source_json,
               t.category_source, t.category_rule_id, t.replaced_by_statement_id
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = ? AND t.deleted_at IS NULL
        ORDER BY t.booking_date DESC, t.id DESC
        """,
        (account_type,),
    ).fetchall()
    return [(row, rule_values(dict(row))) for row in rows]


def backfill_category_sources(db: sqlite3.Connection) -> None:
    """Record how existing transactions (removed ones too) were categorized, conservatively.

    A transaction still in the category and flow of the rule that decides it counts as
    categorized by that rule; one in its account type's Uncategorized as default; anything
    else as set by hand.
    """
    for account_type in ACCOUNT_TYPES:
        if not db.execute("SELECT 1 FROM accounts WHERE account_type = ?", (account_type,)).fetchone():
            continue
        rules = account_rules(db, account_type)
        uncategorized_id = uncategorized_category_id(db, account_type)
        rows = db.execute(
            """
            SELECT t.id, t.merchant, t.description, t.reference, t.category_id, t.flow_type, t.source_json
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = ?
            """,
            (account_type,),
        ).fetchall()
        for row in rows:
            match = matching_rule(rules, dict(row))
            if match and (row["category_id"], row["flow_type"]) == (match.category_id, match.flow_type):
                source, rule_id = "rule", match.id
            elif row["category_id"] in (None, uncategorized_id):
                source, rule_id = "default", None
            else:
                source, rule_id = "manual", None
            db.execute(
                "UPDATE transactions SET category_source = ?, category_rule_id = ? WHERE id = ?",
                (source, rule_id, row["id"]),
            )


def validate_rule(match_text: str, match_fields: list[str], match_mode: str = "contains") -> tuple[str, list[str]]:
    if match_mode not in MATCH_MODES:
        raise ValueError("Match mode must be contains, word or exact")
    normalized_text = match_text.strip()
    if not normalized_text or len(normalized_text) > 120:
        raise ValueError("Rule text must be between 1 and 120 characters")
    if not match_fields or any(field not in RULE_FIELDS for field in match_fields):
        raise ValueError("Select one or more valid match fields")
    return normalized_text, [field for field in RULE_FIELDS if field in set(match_fields)]


def rule_preview(
    transactions: list[tuple[sqlite3.Row, dict[str, str]]], rule: Rule
) -> tuple[list[tuple[sqlite3.Row, dict[str, str]]], dict[str, Any]]:
    """The transactions whose text matches the rule, and a count with up to three samples."""
    matched = [(row, values) for row, values in transactions if rule.matches(values)]
    return matched, {
        "match_count": len(matched),
        # Card settlements replaced by their statement: matched, but not counted in totals.
        "itemized_count": sum(1 for row, _ in matched if row["replaced_by_statement_id"] is not None),
        "samples": [
            {
                "id": row["id"],
                "date": row["booking_date"],
                "merchant": values["merchant"],
                "description": values["description"],
                "amount": format_money(row["amount_cents"]),
            }
            for row, values in matched[:3]
        ],
    }


def preview_category_rule(
    database_path: Path,
    account_type: str,
    match_text: str,
    match_fields: list[str],
    legacy_normalized: bool = False,
    rule_id: Optional[int] = None,
    match_mode: str = "contains",
) -> dict[str, Any]:
    """Count text matches, and how many of those the rule would win at its priority.

    A saved rule keeps its position; a new rule is evaluated after all existing rules.
    """
    normalized_text, normalized_fields = validate_rule(match_text, match_fields, match_mode)
    candidate = Rule(rule_id, normalized_text, tuple(normalized_fields), legacy_normalized, match_mode=match_mode)
    with connect(database_path) as db:
        transactions = account_transactions(db, account_type)
        rules = account_rules(db, account_type)
    position = next((index for index, rule in enumerate(rules) if rule.id == rule_id), len(rules))
    higher_priority = rules[:position]
    matched, preview = rule_preview(transactions, candidate)
    preview["first_match_count"] = sum(
        1 for _, values in matched if first_match(higher_priority, values) is None
    )
    return preview


def _released_by_draft(
    rules: list[Rule],
    position: int,
    transactions: list[tuple[sqlite3.Row, dict[str, str]]],
    candidate: Rule,
) -> list[tuple[sqlite3.Row, dict[str, str], Optional[Rule]]]:
    """Transactions the saved rule categorized that the draft, at its position, no longer decides.

    Each comes with the rule that would decide it instead, or None when no rule matches it.
    """
    draft_rules = [*rules[:position], candidate, *rules[position + 1:]]
    released = []
    for row, values in transactions:
        if row["category_rule_id"] != candidate.id or row["category_source"] != "rule":
            continue
        match = first_match(draft_rules, values)
        if match is not candidate:
            released.append((row, values, match))
    return released


def rule_impact(
    database_path: Path,
    account_type: str,
    match_text: str,
    match_fields: list[str],
    match_mode: str,
    category_id: int,
    flow_type: str,
    rule_id: Optional[int] = None,
    limit: int = 200,
) -> dict[str, Any]:
    """What a rule (saved or draft) would do: its matches, the ones it decides, and changes.

    A transaction is decided by the rule when no rule earlier in priority order matches it;
    applying the rule changes those whose category or flow type differs, except the ones set
    by hand (`manual`). `released` lists the transactions the saved rule categorized that
    the draft would no longer decide; applying moves them to their next matching rule.
    `field_counts` tells, per field, how many transactions contain the text there, whichever
    fields the rule uses. Matches are listed newest first, up to `limit`.
    """
    normalized_text, normalized_fields = validate_rule(match_text, match_fields, match_mode)
    require_flow_type(flow_type)
    with connect(database_path) as db:
        transactions = account_transactions(db, account_type)
        rules = account_rules(db, account_type)
        names = {row["id"]: row["name"] for row in db.execute(
            "SELECT id, name FROM categories WHERE account_type = ?", (account_type,)
        )}
        candidate = Rule(rule_id, normalized_text, tuple(normalized_fields), match_mode=match_mode)
    position = next((index for index, rule in enumerate(rules) if rule.id == rule_id), len(rules))
    released = _released_by_draft(rules, position, transactions, candidate) if position < len(rules) else []
    earlier = rules[:position]
    field_counts = {
        field: sum(1 for _, values in transactions if Rule(None, normalized_text, (field,), match_mode=match_mode).matches(values))
        for field in RULE_FIELDS
    }
    matches = []
    win_count = change_count = 0
    for row, values in transactions:
        if not candidate.matches(values):
            continue
        earlier_match = first_match(earlier, values)
        wins = earlier_match is None
        manual = wins and row["category_source"] == "manual"
        changes = wins and not manual and (row["category_id"] != category_id or row["flow_type"] != flow_type)
        win_count += wins
        change_count += changes
        if len(matches) < limit:
            matches.append({
                "id": row["id"],
                "date": row["booking_date"],
                "amount": format_money(row["amount_cents"]),
                **values,
                "category": names.get(row["category_id"], UNCATEGORIZED),
                "decided_by": None if wins else earlier_match.match_text,
                "changes": changes,
                "manual": manual,
            })
    match_count = sum(1 for _, values in transactions if candidate.matches(values))
    return {
        "match_count": match_count,
        "win_count": win_count,
        "change_count": change_count,
        "field_counts": field_counts,
        "matches": matches,
        "truncated": match_count > len(matches),
        "released": [
            {
                "id": row["id"],
                "date": row["booking_date"],
                "amount": format_money(row["amount_cents"]),
                **values,
                "category": names.get(row["category_id"], UNCATEGORIZED),
                "new_category": other.category_name if other else UNCATEGORIZED,
                "decided_by": other.match_text if other else None,
            }
            for row, values, other in released[:limit]
        ],
        "released_count": len(released),
    }


def deciding_rules(
    rules: list[Rule], transactions: list[tuple[sqlite3.Row, dict[str, str]]]
) -> dict[int, Rule]:
    """The rule that decides each transaction (by transaction id); transactions no rule matches are left out."""
    deciders: dict[int, Rule] = {}
    for row, values in transactions:
        match = first_match(rules, values)
        if match:
            deciders[row["id"]] = match
    return deciders


def first_match_counts(deciders: dict[int, Rule]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for rule in deciders.values():
        if rule.id is not None:
            counts[rule.id] = counts.get(rule.id, 0) + 1
    return counts


BROAD_TEXT_LENGTH = 4
SHADOWED_BY_LIMIT = 3


def rule_health(
    rule: Rule,
    matched: list[tuple[sqlite3.Row, dict[str, str]]],
    deciders: dict[int, Rule],
    order: dict[Optional[int], int],
) -> dict[str, Any]:
    """Signals that a saved rule may need attention, from the transactions it matches.

    `idle`: it decides none of them. `shadowed`: earlier rules decide some of them;
    `shadowed_by` names up to three of those rules with the number they take, most first.
    `broad`: a contains rule with a very short text. `order` gives each rule's position.
    """
    taken: dict[Rule, int] = {}
    decided = 0
    for row, _ in matched:
        decider = deciders[row["id"]]  # a matching transaction always has a deciding rule
        if decider.id == rule.id:
            decided += 1
        else:
            taken[decider] = taken.get(decider, 0) + 1
    health = []
    if decided == 0:
        health.append("idle")
    if taken:
        health.append("shadowed")
    if rule.match_mode == "contains" and len(rule.match_text.strip()) <= BROAD_TEXT_LENGTH:
        health.append("broad")
    shadowed_by = sorted(taken.items(), key=lambda item: (-item[1], order[item[0].id]))[:SHADOWED_BY_LIMIT]
    return {
        "health": health,
        "shadowed_by": [{"id": other.id, "match_text": other.match_text, "count": count} for other, count in shadowed_by],
    }


def save_category_rule(
    database_path: Path,
    account_type: str,
    category_id: int,
    match_text: str,
    match_fields: list[str],
    flow_type: str,
    rule_id: Optional[int] = None,
    match_mode: str = "contains",
) -> dict[str, Any]:
    """Add or update a rule. Transactions stay as they are until the rule is applied."""
    normalized_text, normalized_fields = validate_rule(match_text, match_fields, match_mode)
    require_flow_type(flow_type)
    with connect(database_path) as db:
        category = db.execute(
            "SELECT id FROM categories WHERE id = ? AND account_type = ?",
            (category_id, account_type),
        ).fetchone()
        if category is None:
            raise ValueError("Category does not belong to this account type")
        if rule_id is None:
            priority = db.execute(
                "SELECT COALESCE(MAX(priority), -1) + 1 FROM category_rules WHERE account_type = ?",
                (account_type,),
            ).fetchone()[0]
            cursor = db.execute(
                """
                INSERT INTO category_rules(
                    account_type, category_id, match_text, match_fields, flow_type, priority, match_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_type, category_id, normalized_text, json.dumps(normalized_fields), flow_type, priority, match_mode),
            )
            return {"id": int(cursor.lastrowid)}
        updated = db.execute(
            """
            UPDATE category_rules
            SET category_id = ?, match_text = ?, match_fields = ?, flow_type = ?, match_mode = ?,
                legacy_normalized = 0
            WHERE id = ? AND account_type = ?
            """,
            (category_id, normalized_text, json.dumps(normalized_fields), flow_type, match_mode, rule_id, account_type),
        )
        if updated.rowcount == 0:
            raise LookupError("Rule not found")
    return {"id": rule_id}


def delete_category_rule(database_path: Path, account_type: str, rule_id: int) -> None:
    """Delete a rule and move the transactions it categorized to Uncategorized.

    Transactions categorized by hand or by other rules stay as they are.
    """
    with connect(database_path) as db:
        if db.execute(
            "SELECT 1 FROM category_rules WHERE id = ? AND account_type = ?", (rule_id, account_type)
        ).fetchone() is None:
            raise LookupError("Rule not found")
        uncategorized_id = uncategorized_category_id(db, account_type)
        db.executemany(
            """
            UPDATE transactions
            SET category_id = ?, flow_type = ?, category_source = 'default', category_rule_id = NULL
            WHERE id = ?
            """,
            [
                (uncategorized_id, default_flow_type(int(row["amount_cents"])), row["id"])
                for row, _ in account_transactions(db, account_type)
                if row["category_rule_id"] == rule_id and row["category_source"] == "rule"
            ],
        )
        db.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))


def reorder_category_rules(database_path: Path, account_type: str, rule_ids: list[int]) -> None:
    with connect(database_path) as db:
        current = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM category_rules WHERE account_type = ? ORDER BY priority, id",
                (account_type,),
            )
        ]
        if len(rule_ids) != len(set(rule_ids)) or set(rule_ids) != set(current):
            raise ValueError("Rule order must include every current rule exactly once")
        db.executemany(
            "UPDATE category_rules SET priority = ? WHERE id = ? AND account_type = ?",
            [(position, rule_id, account_type) for position, rule_id in enumerate(rule_ids)],
        )


def run_category_rule(database_path: Path, account_type: str, rule_id: int) -> dict[str, int]:
    """Apply a rule: categorize what it decides, and release what it no longer decides.

    Transactions the rule decides (it is their first match) get its category and flow type,
    unless they were set by hand. Transactions it categorized before that it no longer
    decides move to their current first match, or to Uncategorized. Returns how many
    transactions changed category or flow type (`updated`) and how many were released.
    """
    with connect(database_path) as db:
        rules = account_rules(db, account_type)
        if rule_id not in {rule.id for rule in rules}:
            raise LookupError("Rule not found")
        uncategorized_id = uncategorized_category_id(db, account_type)
        updated = released = 0
        for row, values in account_transactions(db, account_type):
            match = first_match(rules, values)
            if match and match.id == rule_id:
                if row["category_source"] == "manual":
                    continue
                updated += (row["category_id"], row["flow_type"]) != (match.category_id, match.flow_type)
            elif row["category_rule_id"] == rule_id and row["category_source"] == "rule":
                released += 1
            else:
                continue
            if match:
                category_id, flow_type, source = match.category_id, match.flow_type, "rule"
            else:
                category_id, flow_type, source = uncategorized_id, default_flow_type(int(row["amount_cents"])), "default"
            db.execute(
                """
                UPDATE transactions
                SET category_id = ?, flow_type = ?, category_source = ?, category_rule_id = ?
                WHERE id = ?
                """,
                (category_id, flow_type, source, match.id if source == "rule" else None, row["id"]),
            )
        return {"updated": updated, "released": released}
