"""Revolut account statement CSVs (English, comma-separated).

The export names no IBAN, so the account is keyed by a synthetic identifier built from the
bank, product and currency (`REVOLUT-CURRENT-EUR`); later exports map to the same account.
Only COMPLETED rows are imported: pending rows reappear as completed in a later export, and
reverted, declined and failed rows never moved money.
"""

import csv
import io
from datetime import datetime

from .database import parse_money
from .describe import readable

REVOLUT_HEADER_PREFIX = "Type,Product,Started Date,Completed Date,Description"
REVOLUT_COLUMNS = {
    "Type",
    "Product",
    "Started Date",
    "Completed Date",
    "Description",
    "Amount",
    "Fee",
    "Currency",
    "State",
    "Balance",
}
REVOLUT_ACCOUNT_PREFIX = "REVOLUT-"
_DATE_TIME = "%Y-%m-%d %H:%M:%S"
_TRANSFER_PREFIXES = ("Payment from ", "Transfer from ", "To ")


def revolut_account_key(product: str, currency: str) -> str:
    words = "-".join(product.upper().split()) or "ACCOUNT"
    return f"{REVOLUT_ACCOUNT_PREFIX}{words}-{currency.upper()}"


def counterparty_name(row_type: str, description: str) -> str:
    """The counterparty of a row: transfers and top-ups lose their "To ", "Payment from ", or "Transfer from " prefix."""
    if row_type.lower() in {"transfer", "topup"}:
        for prefix in _TRANSFER_PREFIXES:
            if description.startswith(prefix):
                description = description[len(prefix):]
                break
    return readable(description)


def parse_revolut(text: str) -> tuple[str, list[dict[str, str]], list[str]]:
    """Parse a Revolut statement into (account key, rows, unknown columns)."""
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = [header.strip() for header in reader.fieldnames or []]
    reader.fieldnames = headers
    missing = sorted(REVOLUT_COLUMNS - set(headers))
    if missing:
        raise ValueError("Missing required CSV columns: " + ", ".join(missing))
    rows: list[dict[str, str]] = []
    accounts = set()
    for row_number, raw_row in enumerate(reader, start=1):
        if any((value or "").strip() for value in raw_row.get(None) or []):
            raise ValueError(f"Row {row_number} has more fields than the header")
        row = {key: (value or "").strip() for key, value in raw_row.items() if key}
        if row["State"].upper() != "COMPLETED":
            continue
        try:
            started = datetime.strptime(row["Started Date"], _DATE_TIME)
            completed = datetime.strptime(row["Completed Date"], _DATE_TIME)
            amount_cents = parse_money(row["Amount"])
            fee_cents = parse_money(row["Fee"] or "0")
            balance_cents = parse_money(row["Balance"])
        except ValueError as error:
            raise ValueError(f"Invalid data on row {row_number}: {error}") from error
        if row["Currency"] != "EUR":
            raise ValueError(f"Unsupported currency on row {row_number}: {row['Currency']}")
        accounts.add(revolut_account_key(row["Product"], row["Currency"]))
        row["Naam tegenpartij"] = counterparty_name(row["Type"], row["Description"])
        row["Omschrijving"] = row["Description"]
        row["Vrije mededeling"] = row["Type"]
        row["Munt"] = row["Currency"]
        row["_booking_date"] = completed.date().isoformat()
        row["_value_date"] = started.date().isoformat()
        row["_amount_cents"] = str(amount_cents - fee_cents)
        row["_balance_cents"] = str(balance_cents)
        row["_sort"] = completed.isoformat() + started.isoformat()
        rows.append(row)
    if not rows:
        raise ValueError("CSV contains no completed transactions")
    if len(accounts) != 1:
        raise ValueError("A CSV import must contain transactions from exactly one account")
    # Lists show the newest transaction of a day first, as in the other exports.
    rows.sort(key=lambda row: row["_sort"])
    for index, row in enumerate(rows):
        del row["_sort"]
        row["_row_number"] = str(len(rows) - index)
    return next(iter(accounts)), rows, sorted(set(headers) - REVOLUT_COLUMNS)
