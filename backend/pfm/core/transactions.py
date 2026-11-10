"""Transaction listing and category changes."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .database import (
    UNCATEGORIZED,
    connect,
    default_flow_type,
    format_money,
    masked_iban,
    require_flow_type,
    uncategorized_category_id,
)
from .annotations import count_open_follow_ups
from .imports import account_label
from .reports import COUNTED_SQL, OWN_TRANSFER_SQL, REPORT_FLOW_SQL
from .rules import Rule, account_rules, first_match, rule_values, transaction_texts
from .statements import card_account, statement_rows

SEARCHED_COLUMNS = (
    "t.merchant",
    "t.description",
    "t.reference",
    "json_extract(t.source_json, '$.Omschrijving')",
    "json_extract(t.source_json, '$.Mededelingen')",
    "t.note",
)

_NUMBER = r"\d+(?:[.,]\d+)*"
_AMOUNT_WORD = re.compile(rf"^([+-]?)({_NUMBER})$")
_RANGE = re.compile(rf"^([+-]?)({_NUMBER})\s*(?:\.\.|-|\u2013)\s*([+-]?)({_NUMBER})$")


@dataclass(frozen=True)
class AmountCondition:
    """Amounts from low_cents to high_cents inclusive; unsigned ones compare the absolute amount."""

    low_cents: int
    high_cents: int
    signed: bool


@dataclass(frozen=True)
class SearchQuery:
    """A search split into a text part and an optional amount condition.

    A lone amount matches the text OR the amount (match_any); an amount word next to
    other words is an extra filter on the text match (AND); a range has no text.
    """

    text: str
    amount: Optional[AmountCondition] = None
    match_any: bool = False


def parse_amount_cents(number: str) -> Optional[int]:
    """Cents for an unsigned Belgian or plain number, or None when it is not an amount.

    The last separator is the decimal one when both . and , appear. A single separator
    followed by exactly three digits is a thousands separator ("1.250" is 1250); otherwise
    it starts one or two decimals.
    """
    if not re.fullmatch(_NUMBER, number):
        return None
    groups = re.split(r"[.,]", number)
    separators = [c for c in number if c in ".,"]
    decimal = ""
    if len(set(separators)) == 2:
        # The last separator is the decimal one; the other may only group thousands.
        decimal = groups[-1]
        if separators[-1] in number[: -len(decimal) - 1]:
            return None
        groups = groups[:-1]
    elif len(separators) == 1 and len(groups[-1]) != 3:
        decimal = groups.pop()
    if decimal and not 1 <= len(decimal) <= 2:
        return None
    if len(groups) > 1 and (not 1 <= len(groups[0]) <= 3 or groups[0].startswith("0") or any(len(g) != 3 for g in groups[1:])):
        return None
    return int("".join(groups)) * 100 + int(decimal.ljust(2, "0") or 0)


def _signed_cents(sign: str, number: str) -> Optional[int]:
    cents = parse_amount_cents(number)
    if cents is None:
        return None
    return -cents if sign == "-" else cents


def _amount_condition(word: str) -> Optional[AmountCondition]:
    """The condition for one amount or range word ("41,5", "-41", "40..50"), else None."""
    word = word.replace("\u20ac", "").strip()
    single = _AMOUNT_WORD.match(word)
    if single:
        cents = _signed_cents(*single.groups())
        if cents is None:
            return None
        return AmountCondition(cents, cents, bool(single.group(1)))
    ranged = _RANGE.match(word)
    if ranged:
        first = _signed_cents(ranged.group(1), ranged.group(2))
        second = _signed_cents(ranged.group(3), ranged.group(4))
        if first is None or second is None:
            return None
        signed = bool(ranged.group(1) or ranged.group(3))
        return AmountCondition(min(first, second), max(first, second), signed)
    return None


def is_range_word(word: str) -> bool:
    return _RANGE.match(word.replace("\u20ac", "").strip()) is not None


def parse_search(query: str) -> SearchQuery:
    """Split a search into text and an amount condition.

    A query that is one amount ("41,50", "-41") matches the amount or the text (only merchant names
    for numbers of up to three characters, which are mostly amounts);
    a range ("40..50", "40 - 50") matches amounts only; with several words, exactly one
    amount or range word becomes an AND filter next to the text of the other words.
    Anything else is a plain text search.
    """
    query = query.strip()
    words = [word for word in query.split() if word != "\u20ac"]
    if not words:
        return SearchQuery(query)
    whole = _amount_condition(" ".join(words))
    if whole is not None and (len(words) == 1 or is_range_word(" ".join(words))):
        if is_range_word(" ".join(words)):
            return SearchQuery("", whole)
        return SearchQuery(words[0].replace("\u20ac", ""), whole, match_any=True)
    if len(words) > 1:
        found = [(i, _amount_condition(word)) for i, word in enumerate(words)]
        found = [(i, condition) for i, condition in found if condition is not None]
        if len(found) == 1:
            index, condition = found[0]
            return SearchQuery(" ".join(words[:index] + words[index + 1:]), condition)
    return SearchQuery(query)


def list_transactions(
    database_path: Path,
    account_type: str,
    month: Optional[str],
    year: Optional[str],
    query: str,
    limit: int,
    offset: int,
    category_filter: Optional[str] = None,
    flow_type_filter: Optional[str] = None,
    follow_up: Optional[str] = None,
    statement_id: Optional[int] = None,
) -> dict[str, Any]:
    """Transactions newest first, filtered by period, category ("uncategorized" or an id),
    reported flow type (or "own_transfer"), and a search over merchant, description, memo, and note.

    With `follow_up` ("open" or "done") the period is ignored and the entity's follow-ups of all years are
    listed instead: open ones oldest first, done ones most recently done first. With `statement_id` the
    period is ignored too, and only that card statement's purchases are listed.

    Category and flow filters match the report totals, so they leave out transfers between
    own accounts and card settlements replaced by their statement's purchases.
    """
    if follow_up not in (None, "open", "done"):
        raise ValueError("Follow-up must be open or done")
    sql = f"""
        SELECT t.id, t.booking_date, t.value_date, t.amount_cents, t.balance_cents,
               t.currency, t.merchant, t.description, t.reference, t.source_json,
               t.note, t.follow_up, t.follow_up_at,
               t.flow_type, t.category_id, c.name AS category, t.category_source,
               r.match_text AS category_rule, r.id AS category_rule_id,
               (SELECT COUNT(*) FROM category_rules earlier
                WHERE earlier.account_type = r.account_type
                  AND (earlier.priority, earlier.id) <= (r.priority, r.id)) AS category_rule_position,
               a.bank, a.kind, t.counterparty_iban,
               {OWN_TRANSFER_SQL} AS own_transfer, t.replaced_by_statement_id IS NOT NULL AS itemized
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN category_rules r ON r.id = t.category_rule_id
        WHERE a.account_type = ? AND t.deleted_at IS NULL
    """
    params: list[Any] = [account_type]
    if follow_up:
        sql += " AND t.follow_up = ?"
        params.append(follow_up)
    if statement_id is not None:
        sql += " AND t.statement_id = ?"
        params.append(statement_id)
    if not follow_up and statement_id is None:
        if month:
            sql += " AND substr(t.booking_date, 1, 7) = ?"
            params.append(month)
        if year:
            sql += " AND substr(t.booking_date, 1, 4) = ?"
            params.append(year)
    if flow_type_filter == "own_transfer":
        sql += f" AND {OWN_TRANSFER_SQL}"
    elif category_filter is not None or flow_type_filter:
        sql += f" AND {COUNTED_SQL}"
    if category_filter == "uncategorized":
        sql += " AND (t.category_id IS NULL OR c.name = ?)"
        params.append(UNCATEGORIZED)
    elif category_filter is not None:
        sql += " AND t.category_id = ?"
        params.append(int(category_filter))
    if flow_type_filter and flow_type_filter != "own_transfer":
        sql += f" AND {REPORT_FLOW_SQL} = ?"
        params.append(flow_type_filter)
    search = parse_search(query)
    text_sql = "(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in SEARCHED_COLUMNS) + ")"
    amount_sql = ""
    amount_params: list[Any] = []
    if search.amount:
        column = "t.amount_cents" if search.amount.signed else "ABS(t.amount_cents)"
        amount_sql = f"{column} BETWEEN ? AND ?"
        amount_params = [search.amount.low_cents, search.amount.high_cents]
    like_text = "%" + re.sub(r"([\\%_])", r"\\\1", search.text) + "%"
    text_params = [like_text] * len(SEARCHED_COLUMNS)
    if search.amount and not search.text:
        sql += f" AND {amount_sql}"
        params.extend(amount_params)
    elif search.amount and search.match_any and len(search.text) <= 3:
        # A short lone number ("41") is mostly an amount; as text it only matches merchant names (not notes),
        # because descriptions and memos are full of references and card numbers that would drown it.
        # Longer numbers, such as a contract or reference number, also search the full texts.
        sql += f" AND (t.merchant LIKE ? ESCAPE '\\' OR {amount_sql})"
        params.extend([like_text, *amount_params])
    elif search.amount and search.match_any:
        sql += f" AND ({text_sql} OR {amount_sql})"
        params.extend([*text_params, *amount_params])
    elif search.amount:
        sql += f" AND {text_sql} AND {amount_sql}"
        params.extend([*text_params, *amount_params])
    elif search.text:
        sql += f" AND {text_sql}"
        params.extend(text_params)
    order = "t.booking_date DESC, t.row_number ASC"
    if follow_up == "open":
        order = "t.booking_date ASC, t.id ASC"
    elif follow_up == "done":
        order = "t.follow_up_at DESC, t.id DESC"
    with connect(database_path) as db:
        total = int(db.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0])
        rows = db.execute(
            sql + f" ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        open_follow_ups = count_open_follow_ups(db, account_type)
    return {
        "total": total,
        "open_follow_ups": open_follow_ups,
        "items": [
            {
                **{key: row[key] for key in ("id", "booking_date", "value_date", "currency", "flow_type", "category_id", "category", "category_source", "category_rule", "note", "follow_up", "follow_up_at")},
                # The rule's id and 1-based place in its account type's priority order, as the rules view numbers it.
                "category_rule_id": row["category_rule_id"] if row["category_source"] == "rule" else None,
                "category_rule_position": row["category_rule_position"] if row["category_source"] == "rule" else None,
                "account": account_label(row["bank"], row["kind"]),
                "own_transfer": bool(row["own_transfer"]),
                "itemized": bool(row["itemized"]),
                **transaction_texts(dict(row), row["bank"]),
                "counterparty_iban": row["counterparty_iban"],
                "amount": format_money(row["amount_cents"]),
                "balance": format_money(row["balance_cents"]),
            }
            for row in rows
        ],
    }


def update_transaction(database_path: Path, transaction_id: int, changes: dict[str, Any]) -> None:
    """Change a transaction's category or flow type; imported fields stay read-only.

    A category id of None means Uncategorized. Choosing Uncategorized without a flow type
    resets the flow to the amount-sign default. Any such change marks the transaction as
    categorized by hand, so applying rules leaves it alone.
    """
    unexpected = set(changes) - {"flow_type", "category_id"}
    if unexpected:
        raise ValueError("Unsupported transaction fields: " + ", ".join(sorted(unexpected)))
    if not changes:
        raise ValueError("No transaction changes provided")
    with connect(database_path) as db:
        current = db.execute(
            """
            SELECT t.category_id, t.flow_type, t.amount_cents, a.account_type
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE t.id = ? AND t.deleted_at IS NULL
            """,
            (transaction_id,),
        ).fetchone()
        if current is None:
            raise LookupError("Transaction not found")
        flow_type = changes.get("flow_type", current["flow_type"])
        require_flow_type(flow_type)
        category_id = current["category_id"]
        if "category_id" in changes:
            uncategorized_id = uncategorized_category_id(db, current["account_type"])
            category_id = changes["category_id"] if changes["category_id"] is not None else uncategorized_id
            if not db.execute(
                "SELECT 1 FROM categories WHERE id = ? AND account_type = ?",
                (category_id, current["account_type"]),
            ).fetchone():
                raise ValueError("Category does not belong to this account type")
            if category_id == uncategorized_id and "flow_type" not in changes:
                flow_type = default_flow_type(int(current["amount_cents"]))
        db.execute(
            """
            UPDATE transactions
            SET category_id = ?, flow_type = ?, category_source = 'manual', category_rule_id = NULL
            WHERE id = ?
            """,
            (category_id, flow_type, transaction_id),
        )


def bulk_set_category(
    database_path: Path, account_type: str, transaction_ids: list[int], category_id: int
) -> dict[str, int]:
    """Categorize many transactions by hand at once, as `update_transaction` does one by one.

    Every transaction must be an active one of the account type, and the category one of its
    categories. The change is all or nothing. Choosing Uncategorized resets each flow type to
    the amount-sign default; any other category keeps each transaction's flow type.
    """
    ids = sorted(set(transaction_ids))
    if not ids:
        raise ValueError("No transactions selected")
    with connect(database_path) as db:
        if not db.execute(
            "SELECT 1 FROM categories WHERE id = ? AND account_type = ?", (category_id, account_type)
        ).fetchone():
            raise ValueError("Category does not belong to this account type")
        uncategorized_id = uncategorized_category_id(db, account_type)
        marks = ",".join("?" * len(ids))
        rows = db.execute(
            f"""
            SELECT t.id, t.amount_cents, t.flow_type
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = ? AND t.deleted_at IS NULL AND t.id IN ({marks})
            """,
            [account_type, *ids],
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("Some transactions do not belong to this account type")
        db.executemany(
            """
            UPDATE transactions
            SET category_id = ?, flow_type = ?, category_source = 'manual', category_rule_id = NULL
            WHERE id = ?
            """,
            [
                (
                    category_id,
                    default_flow_type(int(row["amount_cents"])) if category_id == uncategorized_id else row["flow_type"],
                    row["id"],
                )
                for row in rows
            ],
        )
    return {"updated": len(rows)}


def _kind(details: str) -> str:
    """What sort of transaction a detail line describes, such as "Card" or "Interest"."""
    return details.split(" · ", 1)[0]


def _alike_key(texts: dict[str, str]) -> tuple[str, str]:
    """What makes transactions alike: their merchant (ignoring case) and their kind."""
    return texts["merchant"].casefold(), _kind(texts["details"])


def _best_category(counts: dict[int, int]) -> Optional[int]:
    """The most used category; counts were filled newest first, so ties favour recent use."""
    return max(counts, key=lambda category_id: counts[category_id], default=None)


def similar_transactions(database_path: Path, transaction_id: int) -> dict[str, Any]:
    """Other transactions like this one, and the category they most often have.

    Transactions are alike when their merchant (as rules see it, ignoring case) and their
    kind (the first part of their detail line, such as "Card" or "Mortgage repayment")
    match, so one bank's interest and loan lines are not mixed up. `merchant_count` counts
    the other transactions with the merchant of any kind: when it exceeds the alike ones,
    a rule on the merchant alone would catch more than these. The suggestion leaves out
    `Uncategorized`; among equally common categories, the most recently used one wins.
    """
    with connect(database_path) as db:
        account = db.execute(
            """
            SELECT a.account_type, a.bank FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE t.id = ? AND t.deleted_at IS NULL
            """,
            (transaction_id,),
        ).fetchone()
        if account is None:
            raise LookupError("Transaction not found")
        uncategorized_id = uncategorized_category_id(db, account["account_type"])
        names = {row["id"]: row["name"] for row in db.execute(
            "SELECT id, name FROM categories WHERE account_type = ?", (account["account_type"],)
        )}
        rows = db.execute(
            """
            SELECT t.id, t.category_id, t.merchant, t.description, t.reference, t.source_json, a.bank
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = ? AND t.deleted_at IS NULL
            ORDER BY t.booking_date DESC, t.id DESC
            """,
            (account["account_type"],),
        ).fetchall()
    texts = {row["id"]: transaction_texts(dict(row), row["bank"]) for row in rows}
    target = texts.get(transaction_id, {"merchant": "", "details": ""})
    merchant, kind = target["merchant"], _kind(target["details"])
    same_merchant = [
        row for row in rows
        if merchant and row["id"] != transaction_id and texts[row["id"]]["merchant"].casefold() == merchant.casefold()
    ]
    others = [
        {"id": row["id"], "category_id": row["category_id"] or uncategorized_id}
        for row in same_merchant if _alike_key(texts[row["id"]])[1] == kind
    ]
    counts: dict[int, int] = {}
    for other in others:
        if other["category_id"] != uncategorized_id:
            counts[other["category_id"]] = counts.get(other["category_id"], 0) + 1
    best = _best_category(counts)
    return {
        "merchant": merchant,
        "kind": kind,
        "merchant_count": len(same_merchant),
        "suggestion": None if best is None else {"category_id": best, "name": names[best], "count": counts[best]},
        "others": others,
    }


def uncategorized_groups(database_path: Path, account_type: str) -> list[dict[str, Any]]:
    """Uncategorized transactions grouped by merchant and kind, largest absolute total first.

    Groups use the same merchant and kind as `similar_transactions`. Transfers between own
    accounts and card settlements replaced by statement purchases are left out, because their
    category is not editable. `suggestion` is the most used category among categorized
    transactions of the same merchant and kind. `transactions` lists the group's transactions
    (newest first) for display. `merchant_rule_matches` counts the transactions a new exact
    merchant rule for the group's merchant would categorize when applied: as the last rule, it
    only takes transactions no existing rule decides, and never ones set by hand.
    """
    with connect(database_path) as db:
        uncategorized_id = uncategorized_category_id(db, account_type)
        names = {row["id"]: row["name"] for row in db.execute(
            "SELECT id, name FROM categories WHERE account_type = ?", (account_type,)
        )}
        rows = db.execute(
            f"""
            SELECT t.id, t.booking_date, t.amount_cents, t.category_id, t.merchant, t.description,
                   t.reference, t.source_json, t.category_source, a.bank,
                   {OWN_TRANSFER_SQL} AS own_transfer, t.replaced_by_statement_id IS NOT NULL AS itemized
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = ? AND t.deleted_at IS NULL
            ORDER BY t.booking_date DESC, t.id DESC
            """,
            (account_type,),
        ).fetchall()
        rules = account_rules(db, account_type)
    # Merchants of the transactions a new last-priority rule could still take.
    merchants = [
        values["merchant"]
        for row, values in ((row, rule_values(dict(row))) for row in rows)
        if row["category_source"] != "manual" and first_match(rules, values) is None
    ]
    category_counts: dict[tuple[str, str], dict[int, int]] = {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        texts = transaction_texts(dict(row), row["bank"])
        key = _alike_key(texts)
        category_id = row["category_id"] or uncategorized_id
        if category_id != uncategorized_id:
            counts = category_counts.setdefault(key, {})
            counts[category_id] = counts.get(category_id, 0) + 1
            continue
        if row["own_transfer"] or row["itemized"]:
            continue
        group = groups.setdefault(key, {
            "merchant": texts["merchant"], "kind": key[1], "count": 0, "total_cents": 0,
            "first_date": row["booking_date"], "last_date": row["booking_date"],
            "transaction_ids": [], "sample_details": [], "transactions": [],
        })
        group["count"] += 1
        group["total_cents"] += int(row["amount_cents"])
        group["first_date"] = min(group["first_date"], row["booking_date"])
        group["last_date"] = max(group["last_date"], row["booking_date"])
        group["transaction_ids"].append(row["id"])
        group["transactions"].append({
            "id": row["id"], "date": row["booking_date"],
            "amount": format_money(row["amount_cents"]), "details": texts["details"],
        })
        if texts["details"] not in group["sample_details"] and len(group["sample_details"]) < 3:
            group["sample_details"].append(texts["details"])
    result = []
    for key, group in groups.items():
        best = _best_category(category_counts.get(key, {}))
        rule = Rule(None, group["merchant"], ("merchant",), match_mode="exact")
        result.append({
            **{k: v for k, v in group.items() if k != "total_cents"},
            "total": format_money(group["total_cents"]),
            "suggestion": None if best is None else {
                "category_id": best, "name": names[best], "count": category_counts[key][best]
            },
            "merchant_rule_matches": sum(1 for merchant in merchants if rule.matches({"merchant": merchant})),
            "_sort": (abs(group["total_cents"]), group["count"]),
        })
    result.sort(key=lambda group: group["_sort"], reverse=True)
    for group in result:
        del group["_sort"]
    return result


def settlement_statement(database_path: Path, transaction_id: int) -> dict[str, Any]:
    """The card statement that replaced a bank settlement, with its card and its purchases as listed transactions."""
    with connect(database_path) as db:
        row = db.execute(
            """
            SELECT s.id, s.account_id, a.account_type
            FROM transactions t JOIN card_statements s ON s.id = t.replaced_by_statement_id
            JOIN accounts a ON a.id = s.account_id
            WHERE t.id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if row is None:
            raise LookupError("This transaction is not an itemized card payment")
        card = card_account(db, row["account_id"])
        statement = next(item for item in statement_rows(db, row["account_id"]) if item["id"] == row["id"])
    purchases = list_transactions(database_path, row["account_type"], None, None, "", 1000, 0, statement_id=row["id"])
    return {**statement, "card": f"{card['bank']} card {masked_iban(card['iban'])}", "purchases": purchases["items"]}
