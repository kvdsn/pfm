"""Copies of imported export files, kept in the source-data folder next to the database."""

import hashlib
from datetime import date
from pathlib import Path
from typing import Optional

from .database import masked_iban
from .parsing import ParsedExport

SOURCE_DIR_NAME = "source-data"


def source_dir(database_path: Path) -> Path:
    return database_path.parent / SOURCE_DIR_NAME


def account_folder_name(bank: str, kind: str, account: str) -> str:
    """Such as "Belfius account 1234", "KBC card 5678" or "Revolut account Current EUR"."""
    suffix = masked_iban(account).replace("•••• ", "")
    return f"{bank} {'card' if kind == 'card' else 'account'} {suffix}"


def source_file_name(parsed: ParsedExport, contents: bytes, today: Optional[date] = None) -> str:
    """A single card statement by its debit date ("statement 2026-09-25.pdf"), else the export's
    booking-date range ("2026-01-02 to 2026-10-16.csv")."""
    extension = ".pdf" if contents.startswith(b"%PDF") else ".csv"
    if len(parsed.statements) == 1:
        return f"statement {parsed.statements[0].debit_date}{extension}"
    dates = sorted(row["_booking_date"] for row in parsed.rows if row.get("_booking_date"))
    if not dates:
        return f"imported {(today or date.today()).isoformat()}{extension}"
    if dates[0] == dates[-1]:
        return f"{dates[0]}{extension}"
    return f"{dates[0]} to {dates[-1]}{extension}"


def _same_contents(path: Path, digest: str) -> bool:
    return hashlib.sha256(path.read_bytes()).hexdigest() == digest


def save_source_file(database_path: Path, account_type: str, parsed: ParsedExport, contents: bytes) -> str:
    """Store a copy of an imported export; returns its path relative to the source-data folder.

    A file with the same contents already in the account's folder is reused. A different file
    with the same name gets a numbered suffix, so nothing is ever overwritten.
    """
    root = source_dir(database_path)
    folder = root / account_type / account_folder_name(parsed.bank, parsed.kind, parsed.account)
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(contents).hexdigest()
    for existing in folder.iterdir():
        if existing.is_file() and existing.stat().st_size == len(contents) and _same_contents(existing, digest):
            return existing.relative_to(root).as_posix()
    name = source_file_name(parsed, contents)
    stem, extension = name.rsplit(".", 1)
    target = folder / name
    number = 2
    while target.exists():
        target = folder / f"{stem} ({number}).{extension}"
        number += 1
    with target.open("xb") as handle:
        handle.write(contents)
    return target.relative_to(root).as_posix()


def move_account_folder(database_path: Path, bank: str, kind: str, account: str, old_type: str, new_type: str) -> None:
    """Follow an account to another account type, merging into any folder already there."""
    root = source_dir(database_path)
    name = account_folder_name(bank, kind, account)
    old, new = root / old_type / name, root / new_type / name
    if not old.is_dir():
        return
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return
    for item in old.iterdir():
        target, number = new / item.name, 2
        while target.exists():
            target = new / f"{item.stem} ({number}){item.suffix}"
            number += 1
        item.rename(target)
    old.rmdir()
