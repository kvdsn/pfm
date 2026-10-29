"""Credit card exports: KBC card CSVs and Belfius card statement PDFs.

Card purchases become transactions of a card account. Each purchase belongs to a monthly
statement; the bank account pays a statement's total in one settlement transaction, which
`statements.refresh_card_settlements` replaces by the statement's purchases.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from .database import parse_money

KBC_CARD_HEADER_PREFIX = "kredietkaart;kaarthouder;uitgavenstaat"
KBC_CARD_COLUMNS = {
    "kredietkaart",
    "kaarthouder",
    "uitgavenstaat",
    "datum verrichting",
    "Datum verrekening",
    "bedrag",
    "munt",
    "koers",
    "bedrag in EUR",
    "Kosten op verrichting",
    "Handelaar",
    "locatie",
    "land",
}
KBC_CARD_SETTLEMENT = "DOMICILIERING VIA JE BANK"
DUTCH_MONTHS = (
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
)


@dataclass(frozen=True)
class Statement:
    """A card statement: its purchases are paid from the bank account around `debit_date`."""

    key: str
    debit_date: str


def _iso(day: str) -> str:
    return datetime.strptime(day.strip(), "%d/%m/%Y").date().isoformat()


def _next_statement_label(label: str) -> Optional[tuple[str, date]]:
    """The label and first day of the month after a KBC statement label like "juli 2026"."""
    month_name, _, year = label.strip().partition(" ")
    if month_name not in DUTCH_MONTHS or not year.isdigit():
        return None
    month = DUTCH_MONTHS.index(month_name) + 1
    next_month = date(int(year) + month // 12, month % 12 + 1, 1)
    return f"{DUTCH_MONTHS[next_month.month - 1]} {next_month.year}", next_month


def parse_kbc_card(text: str) -> tuple[str, list[dict[str, str]], list["Statement"], list[str]]:
    """Parse a KBC credit card export.

    Rows without a card number are the bank's payments of the previous month's statement
    ("DOMICILIERING VIA JE BANK"); they only date the statements and are not imported.
    """
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    headers = [header.strip() for header in reader.fieldnames or []]
    reader.fieldnames = headers
    missing = sorted(KBC_CARD_COLUMNS - set(headers))
    if missing:
        raise ValueError("Missing required card export columns: " + ", ".join(missing))
    rows: list[dict[str, str]] = []
    payment_dates: dict[str, str] = {}
    cards = set()
    for row_number, raw_row in enumerate(reader, start=1):
        if any((value or "").strip() for value in raw_row.get(None) or []):
            raise ValueError(f"Row {row_number} has more fields than the header")
        row = {key: (value or "").strip() for key, value in raw_row.items() if key}
        if not any(row.values()):
            continue
        try:
            amount_cents = parse_money(row["bedrag in EUR"])
            booking_date = _iso(row["datum verrichting"])
            value_date = _iso(row["Datum verrekening"])
        except ValueError as error:
            raise ValueError(f"Invalid data on row {row_number}: {error}") from error
        if not row["kredietkaart"]:
            if row["Handelaar"] == KBC_CARD_SETTLEMENT:
                payment_dates[row["uitgavenstaat"]] = booking_date
                continue
            raise ValueError(f"Row {row_number} has no card number")
        cards.add(row["kredietkaart"])
        notes = []
        if row["munt"] and row["munt"] != "EUR":
            notes.append(f"{row['bedrag'].rstrip('0').rstrip(',')} {row['munt']} at {row['koers'].rstrip('0')}")
        if row["Kosten op verrichting"] and parse_money(row["Kosten op verrichting"]):
            notes.append(f"fee {row['Kosten op verrichting']} EUR")
        rows.append({
            **row,
            "Naam tegenpartij": row["Handelaar"],
            "Omschrijving": " ".join(part for part in (row["Handelaar"], row["locatie"], row["land"]) if part),
            "Vrije mededeling": " · ".join(notes),
            "Munt": "EUR",
            "Valuta": row["Datum verrekening"],
            "_booking_date": booking_date,
            "_value_date": value_date,
            "_amount_cents": str(amount_cents),
            "_balance_cents": "",
            "_row_number": str(row_number),
            "_statement": row["uitgavenstaat"],
        })
    if not rows:
        raise ValueError("Card export contains no card transactions")
    if len(cards) != 1:
        raise ValueError("A card export must contain transactions from exactly one card")
    statements = []
    for label in sorted({row["_statement"] for row in rows}):
        following = _next_statement_label(label)
        if following is None:
            raise ValueError(f"Unknown statement month: {label}")
        next_label, next_month = following
        statements.append(Statement(label, payment_dates.get(next_label, next_month.isoformat())))
    return next(iter(cards)), rows, statements, sorted(set(headers) - KBC_CARD_COLUMNS - {"credit", "debet", "toelichting"})


BELFIUS_ITEM = re.compile(
    r"^(?P<day>\d{2})/(?P<month>\d{2}) (?P<value_day>\d{2})/(?P<value_month>\d{2})\s+(?P<text>.+?)\s+"
    r"(?:(?P<foreign>[\d.]+,\d{2}) (?P<currency>[A-Z]{3}) )?(?P<amount>[\d.]+,\d{2}) EUR ?(?P<sign>[-+])$"
)
BELFIUS_HOLDER = re.compile(r"^Transacties - Kaartnummer .+? - (?P<holder>.+)$")
BELFIUS_VIA = re.compile(r"^\(Via ?(?P<via>[^)]*)\)\s*(?P<rate>1 EUR =)?\s*(?P<rate_value>[\d.,]+[A-Z]{3})?$")
BELFIUS_RATE = re.compile(r"^(?P<rate>[\d.,]+)(?P<currency>[A-Z]{3})$")


def _statement_day(day: int, month: int, period_end: date) -> date:
    """The full date of a day/month on a statement that closes on `period_end`."""
    candidate = date(period_end.year, month, day)
    return candidate if candidate <= period_end + timedelta(days=31) else date(period_end.year - 1, month, day)


def parse_belfius_card_pdf(contents: bytes) -> tuple[str, list[dict[str, str]], list[Statement]]:
    """Parse a Belfius credit card statement ("uitgavenstaat") PDF."""
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - dependency is in requirements.txt
        raise ValueError("Reading PDF statements requires the pypdf package") from error
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(contents)).pages)
    except Exception as error:
        raise ValueError(f"Could not read the PDF: {error}") from error
    reference = re.search(r"Klantenreferentie (\d+)", text)
    closing = re.search(r"Afsluitingsdatum (\d{2}/\d{2}/\d{4})", text)
    debit = re.search(r"Datum van debet (\d{2}/\d{2}/\d{4})", text)
    if not (reference and closing and debit and "UITGAVENSTAAT" in text):
        raise ValueError("PDF is not a Belfius credit card statement")
    period_end = datetime.strptime(closing.group(1), "%d/%m/%Y").date()
    statement = Statement(period_end.isoformat(), _iso(debit.group(1)))

    items: list[dict[str, str]] = []
    holders = []
    holder = ""
    expect_rate = False
    for line in (line.strip() for line in text.splitlines()):
        if match := BELFIUS_HOLDER.match(line):
            holder = match.group("holder").strip()
            if holder not in holders:
                holders.append(holder)
        elif match := BELFIUS_ITEM.match(line):
            booked = _statement_day(int(match.group("day")), int(match.group("month")), period_end)
            settled = _statement_day(int(match.group("value_day")), int(match.group("value_month")), period_end)
            cents = parse_money(match.group("amount"))
            text_part = re.sub(r"\s+", " ", match.group("text"))
            notes = []
            if match.group("foreign"):
                notes.append(f"{match.group('foreign')} {match.group('currency')}")
            items.append({
                "Kaarthouder": holder,
                "Datum transactie": booked.strftime("%d/%m/%Y"),
                "Datum verrekening": settled.strftime("%d/%m/%Y"),
                "Omschrijving": text_part,
                "Bedrag": f"{'-' if match.group('sign') == '-' else ''}{match.group('amount')}",
                "Uitgavenstaat": statement.key,
                "Naam tegenpartij": re.sub(r" [A-Z]{2}$", "", text_part),
                "Munt": "EUR",
                "Valuta": settled.strftime("%d/%m/%Y"),
                "_notes": " · ".join(notes),
                "_booking_date": booked.isoformat(),
                "_value_date": settled.isoformat(),
                "_amount_cents": str(-cents if match.group("sign") == "-" else cents),
                "_balance_cents": "",
                "_row_number": str(len(items) + 1),
                "_statement": statement.key,
            })
            expect_rate = False
        elif items and (match := BELFIUS_VIA.match(line)):
            if match.group("via"):
                items[-1]["_notes"] = " · ".join(filter(None, [f"via {match.group('via').strip()}", items[-1]["_notes"]]))
            expect_rate = bool(match.group("rate")) and not match.group("rate_value")
            if match.group("rate_value"):
                items[-1]["_notes"] += f" at {match.group('rate_value')}"
        elif items and expect_rate and (match := BELFIUS_RATE.match(line)):
            items[-1]["_notes"] += f" (1 EUR = {match.group('rate')} {match.group('currency')})"
            expect_rate = False
    if not items:
        raise ValueError("Card statement contains no transactions")
    for item in items:
        notes = item.pop("_notes")
        if len(holders) > 1 and item["Kaarthouder"]:
            notes = " · ".join(filter(None, [item["Kaarthouder"], notes]))
        item["Vrije mededeling"] = notes
    return reference.group(1), items, [statement]
