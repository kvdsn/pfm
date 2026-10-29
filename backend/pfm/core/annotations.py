"""Notes and follow-up flags on transactions. They never touch the category or any report total."""

import sqlite3
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from .database import connect

MAX_NOTE_LENGTH = 500
FOLLOW_UP_STATES = ("open", "done")


def annotate_transaction(database_path: Path, transaction_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Change a transaction's note and/or follow-up flag and return {note, follow_up, follow_up_at}.

    `follow_up` is None (no flag), "open" or "done". The time is stamped whenever the flag changes to open
    or done, cleared when it is removed, and kept when the flag is set to its current value.
    """
    unexpected = set(changes) - {"note", "follow_up"}
    if unexpected:
        raise ValueError("Unsupported annotation fields: " + ", ".join(sorted(unexpected)))
    if not changes:
        raise ValueError("No annotation changes provided")
    note = None
    if "note" in changes:
        note = (changes["note"] or "").strip()
        if len(note) > MAX_NOTE_LENGTH:
            raise ValueError(f"A note can have at most {MAX_NOTE_LENGTH} characters")
    if "follow_up" in changes and changes["follow_up"] is not None and changes["follow_up"] not in FOLLOW_UP_STATES:
        raise ValueError("Follow-up must be open, done or empty")
    with connect(database_path) as db:
        current = db.execute(
            "SELECT note, follow_up, follow_up_at FROM transactions WHERE id = ? AND deleted_at IS NULL",
            (transaction_id,),
        ).fetchone()
        if current is None:
            raise LookupError("Transaction not found")
        result = {key: current[key] for key in ("note", "follow_up", "follow_up_at")}
        if note is not None:
            result["note"] = note
        if "follow_up" in changes and changes["follow_up"] != current["follow_up"]:
            result["follow_up"] = changes["follow_up"]
            result["follow_up_at"] = (
                datetime.now().astimezone().isoformat() if changes["follow_up"] is not None else None
            )
        db.execute(
            "UPDATE transactions SET note = ?, follow_up = ?, follow_up_at = ? WHERE id = ?",
            (result["note"], result["follow_up"], result["follow_up_at"], transaction_id),
        )
    return result


def count_open_follow_ups(database: Union[Path, sqlite3.Connection], account_type: str) -> int:
    """How many non-deleted transactions of the entity have an open follow-up."""
    with (connect(database) if isinstance(database, Path) else nullcontext(database)) as db:
        return int(
            db.execute(
                """
                SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id
                WHERE a.account_type = ? AND t.follow_up = 'open' AND t.deleted_at IS NULL
                """,
                (account_type,),
            ).fetchone()[0]
        )
