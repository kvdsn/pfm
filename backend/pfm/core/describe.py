"""Short, readable summaries of bank transaction texts.

Bank descriptions repeat dates, card numbers, references, and the memo. `summarize`
extracts who the transaction was with and one line of useful detail; the original texts
stay stored and available unchanged.
"""

import re
from typing import Optional

_SPACES = re.compile(r"\s+")
_REFERENCE_SUFFIX = re.compile(r"\s+REF\. ?: ?\S+(?:\s+VAL\. \d{2}-\d{2})?\s*$")
_DATE_CODE = re.compile(r"\b\d{2}-\d{2}\b")

# KBC card payments: "... OM 15.06 UUR KOFFIE HUIS BE9000 GENT MET APPLE PAY ...".
_KBC_CARD = re.compile(
    r"^(?P<kind>BETALING VIA (?:MAESTRO|DEBIT MASTERCARD|BANCONTACT)|GELDOPNEMING VIA BANCONTACT)"
    r".*? UUR (?P<merchant>.+) (?P<country>[A-Z]{2})(?:\d{4,5})? (?P<city>.+?) MET (?P<rest>.*)$"
)
_KBC_CARD_WITHOUT_PLACE = re.compile(
    r"^(?P<kind>BETALING VIA (?:MAESTRO|DEBIT MASTERCARD|BANCONTACT)|GELDOPNEMING VIA BANCONTACT)"
    r".*? UUR (?P<merchant>.+?) MET (?P<rest>.*)$"
)
_KBC_DIRECT_DEBIT = re.compile(
    r"^EUROPESE DOMICILIERING(?: B2B)?\s+\d{2}-\d{2}\s+SCHULDEISER\s*: (?P<creditor>.+?) REF\. SCHULDEISER"
    r".*?(?:MEDEDELING\s*: (?P<message>.*))?$"
)
_KBC_TRANSFER = re.compile(
    r"^(?P<kind>INSTANTOVERSCHRIJVING|OVERSCHRIJVING|DOORLOPENDE BETALINGSOPDRACHT \d{2}-\d{2} OVERSCHRIJVING)"
    r" (?P<direction>NAAR|VAN) (?:(?:\d{2}-\d{2} )?(?P<iban>[A-Z]{2}\d{2}.+?) BANKIER)?"
)
_KBC_CARD_SETTLEMENT = re.compile(r"^AFREKENING .*?(?P<card>KBC-[A-Z -]+?) UITGAVENSTAAT (?P<number>\d+)")
_KBC_BATCH = re.compile(r"^GEZAMENLIJKE OVERSCHRIJVINGSOPDRACHT \d{2}-\d{2} REFERENTIE (?P<reference>\S+)")

# Belfius card payments: "BANCONTACT-AANKOOP - NAME - 9000 GENT BE - 28/10/25 18:43 - ... - KAART ... - HOLDER".
_BELFIUS_CARD = re.compile(
    r"^BANCONTACT ?-? ?(?P<kind>AANKOOP|GELDOPNEMING|APP OF [A-Z ]+?APP) - (?P<merchant>.+?) - "
    r"(?:(?:\d{4,5}|[A-Z0-9]{2,5}(?: [A-Z]{2})?) (?P<city>.+?) [A-Z]{2} - )?\d{2}/\d{2}/\d{2} \d{2}:\d{2}"
    r"(?P<middle>.*?) - KAART [\dX ]+ - (?P<holder>[^-]+?)(?: REF\.|$)"
)
# Older Belfius card lines: "DEBITMASTERCARD-BETALING [VIA Apple Pay] 08/01 SHOP  CITY ES 33,00 EUR KAART NR ... - HOLDER".
_BELFIUS_DEBIT_MASTERCARD = re.compile(
    r"^(?:BETALING )?DEBITMASTERCARD(?:-BETALING)?(?P<wallet> VIA APPLE PAY)? \d{2}/\d{2} (?P<place>.+?) "
    r"(?P<country>[A-Z]{2}) (?P<amount>[\d.,]+) (?P<currency>[A-Z]{3}) KAART NR [\dX ]+ - (?P<holder>[^-]+?)(?:\s+REF\..*)?$"
)
_BELFIUS_CARD_SETTLEMENT = re.compile(r"^(?P<card>VISA|MASTERCARD) AFREKENING NUMMER (?P<number>\d+)")
_BELFIUS_DIRECT_DEBIT = re.compile(
    r"^UW EUROPESE DOMICILIERING .+? VOOR (?P<creditor>.+?)(?: MEDEDELING: (?P<message>.+?))?(?: REF\.| MANDAAT|$)"
)
_BELFIUS_TRANSFER = re.compile(
    r"^(?P<kind>(?:INSTANT )?(?:OVERSCHRIJVING|STORTING)(?: VIA WERO)?(?: BELFIUS MOBILE| PAYCONIQ| E-COMMERCE)?"
    r" (?:NAAR|VAN)|EUROPESE OVERSCHRIJVING(?: NAAR| VAN)?) "
    r"(?P<rest>.*)$"
)
_WERO = re.compile(r"^AANKOOP VIA WERO (?P<name>.+?) [0-9a-f]{32}")

# Fixed bank charges and loan lines, named by what they are.
_BANK_TEXTS = (
    (re.compile(r"BEROEPSKREDIET", re.I), "Business loan repayment"),
    (re.compile(r"WOONKREDIET", re.I), "Mortgage repayment"),
    (re.compile(r"VERBRUIKSKREDIET", re.I), "Consumer loan repayment"),
    (re.compile(r"INVESTERINGSKREDIET", re.I), "Investment loan fees"),
    (re.compile(r"CODA/XML", re.I), "CODA reporting fee"),
    (re.compile(r"BEHEER DEBETKAART|KOSTPRIJS .*KAART", re.I), "Debit card fee"),
    (re.compile(r"BUSINESS PACK|BEATS STAR|BUSINESS COMPACT|AANVULLENDE BIJDRAGE VOOR UW REKENING", re.I), "Account fee"),
    (re.compile(r"FISCAAL ZEGELRECHT", re.I), "Stamp duty"),
    (re.compile(r"PENSION SAVINGS FUND|PRICOS|PENSIOENSPAREN", re.I), "Pension savings"),
    (re.compile(r"^BETALING LEASING", re.I), "Lease payment"),
    (re.compile(r"^MOBIELE BETALING", re.I), "Mobile payment"),
    (re.compile(r"^INTERESTEN", re.I), "Interest"),
    (re.compile(r"NIET-UITGEVOERDE ORDER", re.I), "Failed order fee"),
)


# Revolut's `Type` column, which its rows keep as the memo, named as the detail line shows it.
_REVOLUT_TYPES = {
    "card payment": "Card",
    "transfer": "Transfer",
    "topup": "Top-up",
    "card refund": "Card refund",
    "refund": "Refund",
    "exchange": "Exchange",
    "fee": "Fee",
}

_ACRONYMS = {"BV", "NV", "VZW", "BVBA", "CVBA", "SRL", "SA", "AG", "KBC", "NMBS", "SNCB", "BTW", "VAT", "UK", "EU"}


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip()


def readable(text: str) -> str:
    """Title-case shouted text ("KOFFIE HUIS" -> "Koffie Huis"); leave mixed case alone."""
    text = _clean(text)
    if not text or text != text.upper() or not any(char.isalpha() for char in text):
        return text
    words = []
    for word in text.split(" "):
        words.append(word if word in _ACRONYMS else word.capitalize())
    return " ".join(words)


# Payment-provider hashes and order codes: long tokens mixing letters and digits.
_CODE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{16,}\b")
_COUNTRY_ONLY = re.compile(r"^[A-Za-z]{2}-?$")


def _join(*parts: Optional[str]) -> str:
    cleaned = (_clean(_CODE.sub("", part or "")) for part in parts)
    return " · ".join(part for part in cleaned if part and not _COUNTRY_ONLY.match(part))


def _first_name(holder: str) -> str:
    """The cardholder's first name from "PEETERS MARIE" or "Peeters Jan"."""
    words = _clean(holder).split(" ")
    return readable(words[-1]) if len(words) > 1 else readable(holder)


def _payment_method(text: str) -> str:
    if "APPLE PAY" in text.upper():
        return "Apple Pay"
    if "VIA INTERNET" in text.upper():
        return "online"
    return ""


def _merchant_info(text: str) -> str:
    """Readable words of a card payment's merchant info, without order codes."""
    words = [word for word in text.split(" ") if not any(char.isdigit() for char in word) and word != ":"]
    info = " ".join(words)
    return "" if len(info) < 4 or "PAYMENT DESCRIPTION" in info.upper() else readable(info)


_CREDITOR_REFERENCE = re.compile(r"\s*(?:SCHULDEISERSREFERTE|REFERTE SCHULDEISER|REF\. SCHULDEISER)\b.*$", re.I)


def _without_prefix(text: str, prefix: str) -> str:
    """`text` without a leading `prefix`, ignoring spacing differences ("SumUp * X" vs "SumUp *X")."""
    wanted = prefix.replace(" ", "").upper()
    consumed = ""
    for index, char in enumerate(text):
        if consumed == wanted:
            return text[index:].strip()
        if char != " ":
            consumed += char.upper()
            if not wanted.startswith(consumed):
                return text
    return "" if consumed == wanted else text


def _split_iban(text: str, name: str) -> tuple[str, str]:
    """The IBAN at the start of a transfer text, and the text after the counterparty name."""
    if name and (index := text.upper().find(name.upper())) > 0:
        return text[:index].strip(), text[index + len(name):]
    match = re.match(r"^([A-Z]{2}\d{2}(?: \d{4}){2,4}(?: \d{1,3})?)\s+(.*)$", text)
    return (match.group(1), match.group(2)) if match else ("", text)


def summarize(merchant: str, description: str, memo: str, bank: str = "") -> tuple[str, str]:
    """A short name for the counterparty and one line of detail, from the original texts."""
    merchant, description, memo = _clean(merchant), _clean(description), _clean(memo)
    upper = description.upper()
    memo_note = "" if not memo or memo.upper() in upper or upper in memo.upper() else memo

    if bank == "Revolut":
        return readable(merchant) or "Revolut", _REVOLUT_TYPES.get(memo.lower(), readable(memo))

    if match := _KBC_CARD.match(upper):
        if match.group("kind").startswith("GELDOPNEMING"):
            return "Cash withdrawal", readable(match.group("city"))
        info = re.search(r"INFO VAN DE HANDELAAR: (.+)$", description)
        return readable(merchant or description[match.start("merchant"):match.end("merchant")]), _join(
            "Card", readable(match.group("city")), _payment_method(match.group("rest")),
            _merchant_info(info.group(1)) if info else "",
        )
    if match := _KBC_CARD_WITHOUT_PLACE.match(upper):
        if match.group("kind").startswith("GELDOPNEMING"):
            return "Cash withdrawal", ""
        return readable(merchant or description[match.start("merchant"):match.end("merchant")]), _join(
            "Card", _payment_method(match.group("rest")),
        )
    if match := _BELFIUS_CARD.match(upper):
        name = merchant or description[match.start("merchant"):match.end("merchant")]
        holder = _first_name(description[match.start("holder"):match.end("holder")])
        if match.group("kind") == "GELDOPNEMING":
            return "Cash withdrawal", _join(readable(match.group("city") or ""), holder)
        if match.group("kind").startswith("APP"):
            return readable(re.sub(r" P2P MOBILE$", "", name, flags=re.I)), _join("Payconiq / app payment", holder)
        extra = [
            part for part in (_clean(piece) for piece in match.group("middle").split(" - "))
            if part and not re.match(r"^(CONTACTLESS|CONTACTLOOS|VIA INTERNET|VIA WALLET)", part)
        ]
        return readable(name), _join(
            "Card", readable(match.group("city") or ""), _payment_method(match.group("middle")),
            *[readable(part) for part in extra], holder,
        )
    if match := _KBC_DIRECT_DEBIT.match(upper):
        message = match.group("message") or ""
        message = "" if message.strip() in {"", "NOT PROVIDED"} else description[match.start("message"):]
        return readable(merchant or match.group("creditor")), _join("Direct debit", message)
    if match := _BELFIUS_DIRECT_DEBIT.match(upper):
        message = description[match.start("message"):match.end("message")] if match.group("message") else memo_note
        return readable(merchant or match.group("creditor")), _join("Direct debit", _CREDITOR_REFERENCE.sub("", message))
    if match := _BELFIUS_DEBIT_MASTERCARD.match(upper):
        place = description[match.start("place"):match.end("place")]
        if merchant:
            place = _without_prefix(place, merchant)
        foreign = f"{match.group('amount')} {match.group('currency')}" if match.group("currency") != "EUR" else ""
        return readable(merchant or place), _join(
            "Card", readable(place) if merchant else "", "Apple Pay" if match.group("wallet") else "", foreign,
            _first_name(description[match.start("holder"):match.end("holder")]),
        )
    if match := _KBC_CARD_SETTLEMENT.match(upper):
        return readable(match.group("card").replace("-", " ")), f"Card statement {match.group('number')}"
    if match := _BELFIUS_CARD_SETTLEMENT.match(upper):
        return f"Belfius {match.group('card').capitalize()}", f"Card statement {match.group('number')}"
    if match := _KBC_BATCH.match(upper):
        return "Batch transfer", f"Reference {match.group('reference')}"
    if match := _WERO.match(description):
        return readable(merchant or match.group("name")), "Wero payment"
    if match := _KBC_TRANSFER.match(upper):
        direction = "to" if match.group("direction") == "NAAR" else "from"
        kind = "Standing order" if match.group("kind").startswith("DOORLOPENDE") else "Transfer"
        return readable(merchant) or "Transfer", _join(f"{kind} {direction} {match.group('iban') or ''}".strip(), memo)
    if match := _BELFIUS_TRANSFER.match(upper):
        direction = "from" if match.group("kind").endswith("VAN") or "STORTING" in match.group("kind") else "to"
        iban, message = _split_iban(description[match.start("rest"):], merchant)
        message = _REFERENCE_SUFFIX.sub("", message)
        message = re.sub(r"\s+NAAR [A-Z]{2}\d{2}(?: \d{4}){3}.*$", "", message).strip(" /-")
        message = re.sub(r"^/[A-Z]/\s*", "", message)
        return readable(merchant) or "Transfer", _join(f"Transfer {direction} {iban}".strip(), message)
    for pattern, label in _BANK_TEXTS:
        if pattern.search(upper):
            return readable(merchant) or bank or _bank_name(upper), label
    if merchant and upper.startswith(merchant.upper()):
        # Card exports: the description is the merchant followed by its location.
        place = description[len(merchant):].strip()
        return readable(merchant), _join(readable(place) if len(place) > 2 else "", memo_note)
    text = _REFERENCE_SUFFIX.sub("", description)
    text = _DATE_CODE.sub("", text, count=1)
    return readable(merchant) or readable(text[:60]) or "—", _join(
        "" if merchant else (readable(text[60:120]) if len(text) > 60 else ""), memo_note,
    )


def _bank_name(upper: str) -> str:
    return "KBC" if "KBC" in upper else "Belfius" if "BELFIUS" in upper or "BEATS" in upper else "Bank"
