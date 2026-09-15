"""Typed parsers for values read off a document.

A value OCR hands back is text, and the question for each field is what that
text is allowed to mean. An equipment count is a whole number or one of the
words the sheets use ("Lot"); a unit price is money; a rating is a number
with a unit. Reading them all as "the first run of digits" turned "1,250"
into 1, "12.5" into 12 and "-3" into 3 -- plausible numbers, silently wrong.

Every parser here returns a `ParsedValue` that keeps the raw text, the
normalized value, a status and the rule that produced it, and never guesses:

  ok        -- the text means exactly this value under the field's contract
  empty     -- nothing was there
  ambiguous -- the text could mean more than one value ("1 250", "2 x 10");
               a person decides
  rejected  -- the text is not a value of this kind ("-3" as a count, "ae")

Only `ok` values are used. The other three are kept, with their rule, so the
row they came from becomes an issue to review instead of a wrong number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Bumped whenever a rule below changes what a text is taken to mean: it is
# part of the provenance kept with every parsed value.
PARSER_RULES_VERSION = "values-2026-09-15.1"

OK = "ok"
EMPTY = "empty"
AMBIGUOUS = "ambiguous"
REJECTED = "rejected"

# The only non-numeric quantities the Design Sheets use.
WORD_QUANTITIES = {"lot", "set", "nos", "no", "pcs", "pc", "ea", "each", "item", "sum"}
# Units that may follow a count in the same cell ("10 Nos", "25m").
COUNT_UNITS = {"nos", "no", "pcs", "pc", "ea", "each", "set", "sets", "m", "mtr", "mtrs", "lm", "roll", "rolls", "lot"}

# A single letter in a quantity cell is a misread digit -- the sheets never
# spell a quantity with one character.
SINGLE_LETTER_DIGITS = {"i": "1", "l": "1", "I": "1", "|": "1", "o": "0", "O": "0", "S": "5", "s": "5"}

# More than this many of one device on one line is not a quantity these
# sheets carry; it is two cells run together or a catalog number.
MAX_EQUIPMENT_COUNT = 999_999

# What surrounds a value without being part of it: the column rule read as a
# pipe, a trailing dot, loose brackets. A minus sign is deliberately absent --
# it is checked before anything is stripped.
_EDGE_JUNK = " \t|_—–:;.,()[]{}'\"`~"
_SIGN_RE = re.compile(r"^[\s|_(\[]*[-−–—]\s*\d")
_MULTIPLIER_RE = re.compile(r"\d\s*[x×X*]\s*\d")


@dataclass(frozen=True)
class ParsedValue:
    kind: str
    raw: str | None
    value: Any
    status: str
    rule: str
    unit: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    def text(self) -> str | None:
        """The value as the BOQ stores it, or None when it is not ok."""
        if not self.ok or self.value is None:
            return None
        return str(self.value)

    def to_dict(self) -> dict:
        value = self.value
        if isinstance(value, Decimal):
            value = str(value)
        return {"kind": self.kind, "raw": self.raw, "value": value, "status": self.status,
                "rule": self.rule, "unit": self.unit, "rules_version": PARSER_RULES_VERSION}


def _empty(kind: str, raw: str | None) -> ParsedValue:
    return ParsedValue(kind, raw, None, EMPTY, "no text")


def _strip_edges(text: str) -> str:
    return text.strip(_EDGE_JUNK)


# --- equipment counts ---------------------------------------------------------------


def parse_quantity(raw: str | None, *, allow_space_grouping: bool = False) -> ParsedValue:
    """An equipment count: a non-negative whole number, or a quantity word.

    `allow_space_grouping` accepts "1 250" as 1250. It is off by default:
    in a quantity strip two numbers side by side are as likely two cells
    run together as one grouped number, and only a column known to print
    grouped integers may read them as one.
    """
    kind = "equipment_count"
    if raw is None or not raw.strip():
        return _empty(kind, raw)
    if _SIGN_RE.match(raw):
        return ParsedValue(kind, raw, None, REJECTED, "negative: a count cannot be below zero")
    text = _strip_edges(raw)
    if not text:
        return _empty(kind, raw)
    if _MULTIPLIER_RE.search(text):
        return ParsedValue(kind, raw, None, AMBIGUOUS, "multiplier: 'a x b' is not one count")

    if text.isdigit():
        return _count(kind, raw, text, "digits")

    # "1,250": comma thousands grouping, every group three digits.
    if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
        return _count(kind, raw, text.replace(",", ""), "comma thousands grouping")
    # "12.5", "0.5", "12,5": a fraction of a device is not a count.
    if re.fullmatch(r"\d+[.,]\d+", text):
        return ParsedValue(kind, raw, None, REJECTED, "decimal: an equipment count is a whole number")
    # "1 250"
    if re.fullmatch(r"\d{1,3}( \d{3})+", text):
        if allow_space_grouping:
            return _count(kind, raw, text.replace(" ", ""), "space thousands grouping")
        return ParsedValue(kind, raw, None, AMBIGUOUS, "space-separated digits: one grouped number or two cells")

    # "10 Nos", "25m"
    with_unit = re.fullmatch(r"(\d+)\s*([A-Za-z]+)\.?", text)
    if with_unit and with_unit.group(2).lower() in COUNT_UNITS:
        parsed = _count(kind, raw, with_unit.group(1), "digits with unit")
        return ParsedValue(parsed.kind, parsed.raw, parsed.value, parsed.status, parsed.rule, with_unit.group(2))

    # "a 759": a scan's border mark read as a lone letter beside the number.
    # Only a letter no digit is mistaken for is dropped -- "l 759" could be
    # 1759, and stays ambiguous.
    tokens = text.split()
    numbers = [t for t in tokens if t.isdigit()]
    stray = [t for t in tokens if not t.isdigit()]
    if (len(numbers) == 1 and stray and all(len(t) == 1 and t.isalpha() and t not in SINGLE_LETTER_DIGITS for t in stray)):
        return _count(kind, raw, numbers[0], f"stray mark {' '.join(stray)!r} beside the number ignored")

    letters = re.sub(r"[^A-Za-z]", "", text)
    if letters and letters == text.replace(" ", "") and letters.lower() in WORD_QUANTITIES:
        return ParsedValue(kind, raw, letters, OK, "quantity word")
    if len(text) == 1 and text in SINGLE_LETTER_DIGITS:
        return ParsedValue(kind, raw, SINGLE_LETTER_DIGITS[text], OK, f"single-letter OCR confusion {text!r}->digit")
    if re.search(r"\d", text):
        return ParsedValue(kind, raw, None, AMBIGUOUS, "digits mixed with other characters")
    return ParsedValue(kind, raw, None, REJECTED, "not a quantity")


def _count(kind: str, raw: str, digits: str, rule: str) -> ParsedValue:
    value = int(digits)
    if value > MAX_EQUIPMENT_COUNT:
        return ParsedValue(kind, raw, None, REJECTED, f"overflow: more than {MAX_EQUIPMENT_COUNT:,} on one line")
    return ParsedValue(kind, raw, str(value), OK, rule)


def is_quantity(text: str | None) -> bool:
    """Whether a stored or typed quantity is one the BOQ accepts."""
    return parse_quantity(text).ok


# --- decimals, money, ratings ----------------------------------------------------------


def parse_decimal(raw: str | None, *, kind: str = "decimal", allow_negative: bool = False) -> ParsedValue:
    """A decimal number, kept exact. Grouping commas are accepted only in
    groups of three before a decimal point; a lone decimal comma ("12,5")
    is ambiguous, since the archive mixes both conventions."""
    if raw is None or not raw.strip():
        return _empty(kind, raw)
    negative = bool(_SIGN_RE.match(raw))
    text = _strip_edges(raw.replace("−", "-"))
    text = text.lstrip("-–—").strip()
    if not text:
        return _empty(kind, raw)
    if negative and not allow_negative:
        return ParsedValue(kind, raw, None, REJECTED, "negative value not allowed")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", text):
        normalized, rule = text.replace(",", ""), "comma thousands grouping"
    elif re.fullmatch(r"\d+(\.\d+)?", text):
        normalized, rule = text, "decimal"
    elif re.fullmatch(r"\d+,\d{1,2}", text):
        return ParsedValue(kind, raw, None, AMBIGUOUS, "decimal comma or grouping")
    else:
        return ParsedValue(kind, raw, None, REJECTED, "not a number")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return ParsedValue(kind, raw, None, REJECTED, "not a number")
    return ParsedValue(kind, raw, -value if negative else value, OK, rule)


_CURRENCY_RE = re.compile(r"\b(aed|dhs?|dirhams?|usd|us\$)\b|\$", re.IGNORECASE)


def parse_money(raw: str | None) -> ParsedValue:
    """A price: a non-negative decimal, currency markers removed."""
    if raw is None or not raw.strip():
        return _empty("money", raw)
    stripped = _CURRENCY_RE.sub("", raw).strip()
    parsed = parse_decimal(stripped, kind="money")
    if parsed.ok and parsed.value.as_tuple().exponent < -2:
        return ParsedValue("money", raw, None, REJECTED, "more than two decimal places")
    return ParsedValue("money", raw, parsed.value, parsed.status, parsed.rule)


_RATING_RE = re.compile(r"^\s*([-−]?[\d.,]+)\s*([a-zA-Zµ]+)?\s*([a-zA-Z/ ]*)$")

_UNITS: dict[str, dict[str, tuple[str, Decimal]]] = {
    "voltage": {"v": ("V", Decimal(1)), "vdc": ("V", Decimal(1)), "vac": ("V", Decimal(1)), "mv": ("V", Decimal("0.001")),
                "kv": ("V", Decimal(1000))},
    "current": {"a": ("A", Decimal(1)), "amp": ("A", Decimal(1)), "amps": ("A", Decimal(1)), "ma": ("A", Decimal("0.001")),
                "µa": ("A", Decimal("0.000001")), "ua": ("A", Decimal("0.000001")), "ah": ("Ah", Decimal(1))},
    "power": {"w": ("W", Decimal(1)), "kw": ("W", Decimal(1000)), "va": ("VA", Decimal(1))},
}


def _parse_rating(raw: str | None, kind: str, *, default_unit: str | None) -> ParsedValue:
    if raw is None or not raw.strip():
        return _empty(kind, raw)
    match = _RATING_RE.match(raw.replace(" dc", "dc").replace(" DC", "DC").replace(" ac", "ac").replace(" AC", "AC"))
    if not match:
        return ParsedValue(kind, raw, None, REJECTED, f"not a {kind} rating")
    number = parse_decimal(match.group(1), kind=kind)
    if not number.ok:
        return ParsedValue(kind, raw, None, number.status, number.rule)
    unit_text = (match.group(2) or "").lower()
    if not unit_text:
        if default_unit is None:
            return ParsedValue(kind, raw, None, AMBIGUOUS, "no unit")
        return ParsedValue(kind, raw, number.value, OK, "no unit: column default", default_unit)
    known = _UNITS[kind].get(unit_text)
    if known is None:
        return ParsedValue(kind, raw, None, REJECTED, f"unit {match.group(2)!r} is not a {kind} unit")
    unit, factor = known
    value = number.value * factor
    rule = "as written" if factor == 1 else f"{match.group(2)} converted to {unit}"
    return ParsedValue(kind, raw, value.normalize() if factor != 1 else value, OK, rule, unit)


def parse_voltage(raw: str | None, *, default_unit: str | None = None) -> ParsedValue:
    return _parse_rating(raw, "voltage", default_unit=default_unit)


def parse_current(raw: str | None, *, default_unit: str | None = None) -> ParsedValue:
    """A current in amps: "150 mA" is 0.15 A, with the conversion recorded."""
    return _parse_rating(raw, "current", default_unit=default_unit)


def parse_power(raw: str | None, *, default_unit: str | None = None) -> ParsedValue:
    return _parse_rating(raw, "power", default_unit=default_unit)


_REVISION_RE = re.compile(r"^\s*(?:rev(?:ision)?|r)\s*[.\-_:#]?\s*([0-9]{1,3}|[a-z])\s*$", re.IGNORECASE)


def parse_revision(raw: str | None) -> ParsedValue:
    """A document revision: "R00", "Rev 1", "REV-A" -> "R01" / "RA"."""
    if raw is None or not raw.strip():
        return _empty("revision", raw)
    match = _REVISION_RE.match(raw)
    if not match:
        return ParsedValue("revision", raw, None, REJECTED, "not a revision mark")
    token = match.group(1)
    canonical = f"R{int(token):02d}" if token.isdigit() else f"R{token.upper()}"
    return ParsedValue("revision", raw, canonical, OK, "revision mark")


def parse_text(raw: str | None) -> ParsedValue:
    """Free text: whitespace collapsed, nothing else changed."""
    if raw is None or not raw.strip():
        return _empty("text", raw)
    return ParsedValue("text", raw, re.sub(r"\s+", " ", raw).strip(), OK, "whitespace collapsed")
