import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .card_exports import KBC_CARD_HEADER_PREFIX, Statement, parse_belfius_card_pdf, parse_kbc_card
from .database import parse_money
from .revolut import REVOLUT_HEADER_PREFIX, parse_revolut

MAX_IMPORT_BYTES = 10 * 1024 * 1024
REQUIRED_COLUMNS = {
    "Rekeningnummer",
    "Munt",
    "Datum",
    "Omschrijving",
    "Valuta",
    "Bedrag",
    "Saldo",
    "Naam tegenpartij",
    "Vrije mededeling",
}
KNOWN_COLUMNS = {
    "Rekeningnummer",
    "Rubrieknaam",
    "Naam",
    "Munt",
    "Afschriftnummer",
    "Datum",
    "Omschrijving",
    "Valuta",
    "Bedrag",
    "Saldo",
    "credit",
    "debet",
    "rekeningnummer tegenpartij",
    "BIC tegenpartij",
    "Naam tegenpartij",
    "Adres tegenpartij",
    "gestructureerde mededeling",
    "Vrije mededeling",
}
BELFIUS_HEADER_PREFIX = "Rekening;Boekingsdatum"
BELFIUS_KNOWN_COLUMNS = {
    "Rekening",
    "Boekingsdatum",
    "Rekeninguittrekselnummer",
    "Transactienummer",
    "Rekening tegenpartij",
    "Naam tegenpartij bevat",
    "Straat en nummer",
    "Postcode en gemeente",
    "Transactie",
    "Valutadatum",
    "Bedrag",
    "Devies",
    "BIC",
    "Landcode",
    "Mededelingen",
}
BELFIUS_SALDO_KEY = "Laatste saldo"
BELFIUS_SALDO_MOMENT_KEY = "Datum/uur van het laatste saldo"
BELFIUS_RANGE_END_KEY = "Boekingsdatum tot en met"
# Belfius fills these in only once a transaction is on a statement, so an earlier export has them blank.
BELFIUS_STATEMENT_COLUMNS = ("Rekeninguittrekselnummer", "Transactienummer")


def _belfius_saldo_anchor(preamble: dict[str, str]) -> Optional[int]:
    amount_text, _, currency = preamble.get(BELFIUS_SALDO_KEY, "").rpartition(" ")
    try:
        saldo_cents = parse_money(amount_text)
        saldo_moment = datetime.strptime(
            preamble.get(BELFIUS_SALDO_MOMENT_KEY, ""), "%d/%m/%Y %H:%M:%S"
        )
        range_end = datetime.strptime(
            preamble.get(BELFIUS_RANGE_END_KEY, ""), "%d/%m/%Y"
        )
    except ValueError:
        return None
    if currency != "EUR" or saldo_cents < 0:
        return None
    if range_end.date() < saldo_moment.date():
        return None
    return saldo_cents


def _parse_belfius(
    lines: list[str], header_index: int
) -> tuple[str, list[dict[str, str]], list[str]]:
    preamble: dict[str, str] = {}
    for line in lines[:header_index]:
        key, separator, value = line.partition(";")
        if separator:
            preamble[key.strip()] = value.strip()
    reader = csv.DictReader(
        io.StringIO("\r\n".join(lines[header_index:]), newline=""), delimiter=";"
    )
    headers = reader.fieldnames or []
    missing = sorted(BELFIUS_KNOWN_COLUMNS - set(headers))
    if missing:
        raise ValueError("Missing required CSV columns: " + ", ".join(missing))
    rows = []
    accounts = set()
    for row_number, raw_row in enumerate(reader, start=1):
        if raw_row.get(None):
            raise ValueError(f"Row {row_number} has more fields than the header")
        row = {key: (value or "").strip() for key, value in raw_row.items() if key}
        account = row["Rekening"].replace(" ", "").upper()
        if not account:
            raise ValueError(f"Row {row_number} has no account identifier")
        try:
            booking_date = datetime.strptime(row["Boekingsdatum"], "%d/%m/%Y").date()
            if row["Valutadatum"]:
                datetime.strptime(row["Valutadatum"], "%d/%m/%Y").date()
            parse_money(row["Bedrag"])
        except ValueError as error:
            raise ValueError(f"Invalid data on row {row_number}: {error}") from error
        if row["Devies"] != "EUR":
            raise ValueError(f"Unsupported currency on row {row_number}: {row['Devies']}")
        accounts.add(account)
        row["Naam tegenpartij"] = row["Naam tegenpartij bevat"]
        row["Omschrijving"] = row["Transactie"]
        row["rekeningnummer tegenpartij"] = row["Rekening tegenpartij"].replace(" ", "")
        row["Munt"] = row["Devies"]
        row["Valuta"] = row["Valutadatum"] or row["Boekingsdatum"]
        row["_booking_date"] = booking_date.isoformat()
        row["_amount_cents"] = str(parse_money(row["Bedrag"]))
        row["_balance_cents"] = ""
        row["_chain_anchor"] = "1"
        row["_row_number"] = str(row_number)
        rows.append(row)
    if not rows:
        raise ValueError("CSV contains no transactions")
    if len(accounts) != 1:
        raise ValueError("A CSV import must contain transactions from exactly one account")
    anchor_cents = _belfius_saldo_anchor(preamble)
    if anchor_cents is not None:
        balance = anchor_cents
        for row in rows:
            row["_balance_cents"] = str(balance)
            balance -= int(row["_amount_cents"])
            row.pop("_chain_anchor")
    return next(iter(accounts)), rows, sorted(set(headers) - BELFIUS_KNOWN_COLUMNS)


@dataclass
class ParsedExport:
    """One account's transactions from a bank or card export."""

    account: str
    bank: str
    kind: str
    rows: list[dict[str, str]]
    unknown_columns: list[str]
    statements: list[Statement] = field(default_factory=list)


def parse_export(contents: bytes) -> ParsedExport:
    """Parse a KBC, Belfius or Revolut account CSV, a KBC card CSV, or a Belfius card statement PDF."""
    if len(contents) > MAX_IMPORT_BYTES:
        raise ValueError("File exceeds the 10 MB import limit")
    if contents.startswith(b"%PDF"):
        account, rows, statements = parse_belfius_card_pdf(contents)
        parsed = ParsedExport(account, "Belfius", "card", rows, [], statements)
    else:
        parsed = _parse_csv(contents)
    _number_identical_rows(parsed.rows)
    return parsed


def _number_identical_rows(rows: list[dict[str, str]]) -> None:
    """Give repeated identical rows (two equal purchases on one day) distinct fingerprints."""
    seen: dict[str, int] = {}
    for row in rows:
        fingerprint = transaction_fingerprint(row)
        count = seen.get(fingerprint, 0)
        seen[fingerprint] = count + 1
        if count:
            row["_occurrence"] = str(count)


def _parse_csv(contents: bytes) -> ParsedExport:
    text = None
    decode_error = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = contents.decode(encoding)
            break
        except UnicodeDecodeError as error:
            decode_error = error
    if text is None:
        raise ValueError("CSV text encoding is not supported") from decode_error

    lines = text.splitlines()
    if lines and lines[0].startswith(REVOLUT_HEADER_PREFIX):
        try:
            account, rows, unknown = parse_revolut(text)
        except csv.Error as error:
            raise ValueError(f"Could not parse CSV: {error}") from error
        return ParsedExport(account, "Revolut", "bank", rows, unknown)
    belfius_header_index = next(
        (
            index
            for index, line in enumerate(lines[:30])
            if line.startswith(BELFIUS_HEADER_PREFIX)
        ),
        None,
    )
    if belfius_header_index is not None:
        account, rows, unknown = _parse_belfius(lines, belfius_header_index)
        return ParsedExport(account, "Belfius", "bank", rows, unknown)
    if lines and lines[0].startswith(KBC_CARD_HEADER_PREFIX):
        try:
            account, rows, statements, unknown = parse_kbc_card("\r\n".join(lines))
        except csv.Error as error:
            raise ValueError(f"Could not parse CSV: {error}") from error
        return ParsedExport(account, "KBC", "card", rows, unknown, statements)

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
        headers = reader.fieldnames or []
        if not headers:
            raise ValueError("CSV is empty or has no header row")
        missing = sorted(REQUIRED_COLUMNS - set(headers))
        if missing:
            raise ValueError("Missing required CSV columns: " + ", ".join(missing))
        rows = []
        for row_number, raw_row in enumerate(reader, start=1):
            if raw_row.get(None):
                raise ValueError(f"Row {row_number} has more fields than the header")
            row = {key: (value or "").strip() for key, value in raw_row.items() if key}
            account = row["Rekeningnummer"].replace(" ", "").upper()
            if not account:
                raise ValueError(f"Row {row_number} has no account identifier")
            try:
                booking_date = datetime.strptime(row["Datum"], "%d/%m/%Y").date()
                datetime.strptime(row["Valuta"], "%d/%m/%Y").date()
                parse_money(row["Bedrag"])
                parse_money(row["Saldo"])
            except ValueError as error:
                raise ValueError(f"Invalid data on row {row_number}: {error}") from error
            if row["Munt"] != "EUR":
                raise ValueError(f"Unsupported currency on row {row_number}: {row['Munt']}")
            row["_booking_date"] = booking_date.isoformat()
            row["_amount_cents"] = str(parse_money(row["Bedrag"]))
            row["_balance_cents"] = str(parse_money(row["Saldo"]))
            row["_row_number"] = str(row_number)
            rows.append(row)
        if not rows:
            raise ValueError("CSV contains no transactions")
    except csv.Error as error:
        raise ValueError(f"Could not parse CSV: {error}") from error

    accounts = {row["Rekeningnummer"].replace(" ", "").upper() for row in rows}
    if len(accounts) != 1:
        raise ValueError("A CSV import must contain transactions from exactly one account")
    return ParsedExport(next(iter(accounts)), "KBC", "bank", rows, sorted(set(headers) - KNOWN_COLUMNS))


def pending_fingerprints(rows: list[dict[str, str]]) -> list[Optional[str]]:
    """Per row, the fingerprint it had in an export taken before Belfius numbered it, with repeats numbered like
    `_number_identical_rows`; None for a row without a statement or transaction number."""
    seen: dict[str, int] = {}
    fingerprints: list[Optional[str]] = []
    for row in rows:
        if not any(row.get(column) for column in BELFIUS_STATEMENT_COLUMNS):
            fingerprints.append(None)
            continue
        pending = {key: value for key, value in row.items() if not key.startswith("_")}
        pending.update({column: "" for column in BELFIUS_STATEMENT_COLUMNS})
        fingerprint = transaction_fingerprint(pending)
        count = seen.get(fingerprint, 0)
        seen[fingerprint] = count + 1
        if count:
            fingerprint = transaction_fingerprint({**pending, "_occurrence": str(count)})
        fingerprints.append(fingerprint)
    return fingerprints


def transaction_fingerprint(row: dict[str, str]) -> str:
    raw = {key: value for key, value in row.items() if not key.startswith("_")}
    if row.get("_occurrence"):
        raw["_occurrence"] = row["_occurrence"]
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

