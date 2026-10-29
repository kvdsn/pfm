"""Build a demo database with made-up accounts, transactions, categories and rules.

Run `python -m pfm.demo [folder]` (default `data/demo`), then start the app with `PFM_HOME` set to that folder.
Everything here is invented: the names, account numbers and amounts belong to no one.
"""

import argparse
import csv
import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import core

PERSONAL = "BE00000000001001"
SAVINGS = "BE00000000001002"
BUSINESS = "BE00000000002001"
KIDS = "BE00000000003001"
OWNER = "Sam Demo"
DAY_RATE = 600

HEADERS = [
    "Rekeningnummer", "Rubrieknaam", "Naam", "Munt", "Afschriftnummer", "Datum", "Omschrijving", "Valuta",
    "Bedrag", "Saldo", "credit", "debet", "rekeningnummer tegenpartij", "BIC tegenpartij", "Naam tegenpartij",
    "Adres tegenpartij", "gestructureerde mededeling", "Vrije mededeling",
]

# Per entity: each category with whether it counts as income, and the merchant texts its rule matches.
CATEGORIES = {
    "personal": [
        ("Salary", True, [OWNER + " BV"]),
        ("Child benefit", True, ["Groeipakket Fonds"]),
        ("Housing", False, ["Immo Zonneweg"]),
        ("Groceries", False, ["Colruyt", "Delhaize", "Aldi"]),
        ("Dining out", False, ["Pizzeria Napoli", "Cafe De Hoek", "Sushi Zen"]),
        ("Transport", False, ["TotalEnergies"]),
        ("Utilities", False, ["Lumina Energie", "Aquaflow"]),
        ("Streaming", False, ["Netflix", "Spotify"]),
        ("Insurance", False, ["Veilig Verzekeringen"]),
        ("Health & medical", False, ["Apotheek Centrum"]),
        ("Kids", False, ["Kids account"]),
        ("Taxes", False, ["Vlaamse Belastingdienst"]),
        ("Entity transfer", True, []),
    ],
    "business": [
        ("Consulting income", True, ["Acme Industries"]),
        ("VAT", False, ["BTW-AANGIFTE"]),
        ("Corporate tax", False, ["VOORAFBETALING"]),
        ("Director remuneration", False, ["BEZOLDIGING"]),
        ("Accounting", False, ["Boekhouding Plus"]),
        ("Software", False, ["CloudSoft"]),
        ("Telecom", False, ["Telco Mobile"]),
        ("Bank fees", False, ["BEHEERSKOSTEN"]),
        ("Entity transfer", False, []),
    ],
    "kids": [
        ("Top-ups", True, [OWNER]),
        ("Snacks", False, ["Snoepwinkel"]),
        ("Fun", False, ["Game Store", "Bioscoop"]),
    ],
}


class Account:
    """One made-up bank account: its rows as a KBC export, with a running balance."""

    def __init__(self, iban: str, opening: int):
        self.iban = iban
        self.balance = opening
        self.rows: list[dict[str, str]] = []

    def book(self, day: date, cents: int, merchant: str, description: str = "", counterparty: str = "") -> None:
        self.balance += cents
        self.rows.append({
            "Rekeningnummer": self.iban,
            "Munt": "EUR",
            "Afschriftnummer": str(len(self.rows) + 1),
            "Datum": day.strftime("%d/%m/%Y"),
            "Valuta": day.strftime("%d/%m/%Y"),
            "Bedrag": _money(cents),
            "Saldo": _money(self.balance),
            "Naam tegenpartij": merchant,
            "rekeningnummer tegenpartij": counterparty,
            "Omschrijving": description or merchant.upper(),
        })

    def csv(self) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=HEADERS, delimiter=";")
        writer.writeheader()
        for row in self.rows:
            writer.writerow({header: row.get(header, "") for header in HEADERS})
        return output.getvalue().encode("utf-8-sig")


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",")


def _euros(low: float, high: float, rng: random.Random) -> int:
    return round(rng.uniform(low, high) * 100)


def _last_weekday(year: int, month: int) -> date:
    day = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _transactions(today: date, rng: random.Random) -> dict[str, Account]:
    """Two years of made-up activity, from 1 January last year up to today."""
    personal, savings = Account(PERSONAL, 400000), Account(SAVINGS, 1000000)
    business, kids = Account(BUSINESS, 1500000), Account(KIDS, 10000)
    vat_due: Optional[tuple[date, int]] = None
    year, month = today.year - 1, 1
    while (year, month) <= (today.year, today.month):
        def on(day: int) -> Optional[date]:
            booked = date(year, month, day)
            return booked if booked <= today else None

        if d := on(1):
            personal.book(d, -95000, "Immo Zonneweg", "HUUR")
        if d := on(2):
            personal.book(d, -20000, OWNER, "SPAREN", SAVINGS)
            savings.book(d, 20000, OWNER, "SPAREN", PERSONAL)
        if d := on(3):
            personal.book(d, 19000, "Groeipakket Fonds")
        if d := on(5):
            personal.book(d, -1599, "Netflix")
        if d := on(8):
            personal.book(d, -_euros(95, 140, rng), "Lumina Energie", "DOMICILIERING LUMINA ENERGIE")
        if d := on(12):
            personal.book(d, -1199, "Spotify")
        if d := on(15):
            personal.book(d, -4500, "Veilig Verzekeringen")
            business.book(d, -2900, "CloudSoft")
            business.book(d, -3500, "Telco Mobile")
        if vat_due and (d := on(15)) and vat_due[0] < d:
            business.book(d, -vat_due[1], "FOD Financien", "BTW-AANGIFTE")
            vat_due = None
        if month % 3 == 0 and (d := on(20)):
            personal.book(d, -_euros(50, 70, rng), "Aquaflow")
            business.book(d, -45000, "Boekhouding Plus")
        if month == 7 and (d := on(10)):
            personal.book(d, -26000, "Vlaamse Belastingdienst", "VERKEERSBELASTING")
        if month == 12 and (d := on(20)):
            business.book(d, -250000, "FOD Financien", "VOORAFBETALING VENNOOTSCHAPSBELASTING")
        if d := on(25):
            business.book(d, -320000, OWNER, "BEZOLDIGING", PERSONAL)
            personal.book(d, 320000, OWNER + " BV", "BEZOLDIGING", BUSINESS)
        if d := on(28):
            personal.book(d, -5000, "Kids account", "ZAKGELD", KIDS)
            kids.book(d, 5000, OWNER, "ZAKGELD", PERSONAL)
            business.book(d, -850, "KBC", "BEHEERSKOSTEN")
        if (d := _last_weekday(year, month)) <= today:
            payment = round(rng.choice([17, 18, 19, 20, 20.5, 21]) * DAY_RATE * 121)
            business.book(d, payment, "Acme Industries NV", "FACTUUR CONSULTING")
            vat_due = (d, round(payment * 21 / 121))

        days = [day for day in range(1, 29) if on(day)]
        for merchant, count, low, high in (
            ("Colruyt", 5, 25, 110), ("Delhaize", 3, 15, 60), ("Aldi", 2, 20, 50),
            ("Pizzeria Napoli", 1, 25, 45), ("Cafe De Hoek", 2, 8, 25), ("Sushi Zen", 1, 30, 60),
            ("TotalEnergies", 2, 50, 75),
        ):
            for _ in range(count):
                if days:
                    personal.book(date(year, month, rng.choice(days)), -_euros(low, high, rng), merchant)
        for merchant, low, high in (("Snoepwinkel", 2, 6), ("Game Store", 10, 25), ("Bioscoop", 8, 12)):
            if days and rng.random() < 0.6:
                kids.book(date(year, month, rng.choice(days)), -_euros(low, high, rng), merchant)
        if days and rng.random() < 0.3:
            personal.book(date(year, month, rng.choice(days)), -_euros(8, 30, rng), "Apotheek Centrum")
        if days and rng.random() < 0.25:
            personal.book(date(year, month, rng.choice(days)), -_euros(10, 80, rng), "Marktkraam Jos")
        if days and rng.random() < 0.15:
            personal.book(date(year, month, rng.choice(days)), _euros(5, 30, rng), "Colruyt", "TERUGBETALING")
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)
    return {"personal": personal, "savings": savings, "business": business, "kids": kids}


def build_demo(folder: Path, today: Optional[date] = None, seed: int = 7) -> Path:
    """Create a demo database in `folder`; refuses to touch a folder that already has one."""
    today = today or date.today()
    database = folder / "finance.sqlite3"
    if database.exists():
        raise FileExistsError(f"{database} already exists; delete the folder to build the demo again")
    core.initialize(database)
    ids = {}
    for entity, categories in CATEGORIES.items():
        for name, is_income, texts in categories:
            category_id = ids[entity, name] = core.add_category(database, entity, name)["id"]
            core.set_category_income(database, category_id, is_income)
            for text in texts:
                fields = ["description"] if text.isupper() else ["merchant"]
                core.save_category_rule(database, entity, category_id, text, fields, "income" if is_income else "expense")

    staging: dict = {}
    accounts = _transactions(today, random.Random(seed))
    for name, entity in (("personal", "personal"), ("savings", "personal"), ("business", "business"), ("kids", "kids")):
        preview = core.stage_import(database, f"demo-{name}.csv", accounts[name].csv(), staging)
        core.commit_import(database, preview["token"], entity, staging)

    for year in (today.year, today.year + 1):
        core.set_salary(database, str(year), 3200, 12)
    core.set_day_rate(database, f"{today.year - 1}-01-01", DAY_RATE)
    core.set_budget(database, "personal", ids["personal", "Dining out"], str(today.year), 1500)
    return database


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a demo database with made-up data.")
    default = Path(__file__).resolve().parents[2] / "data" / "demo"
    parser.add_argument("folder", nargs="?", type=Path, default=default, help=f"where to create it (default {default})")
    folder = parser.parse_args().folder
    try:
        database = build_demo(folder)
    except FileExistsError as error:
        sys.exit(str(error))
    print(f"Demo database created: {database}")
    print(f"Start the app on it with: PFM_HOME={folder} PYTHONPATH=backend .venv/bin/python -m pfm")


if __name__ == "__main__":
    main()
