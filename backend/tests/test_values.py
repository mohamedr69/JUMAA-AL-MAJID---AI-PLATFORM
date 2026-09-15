"""Typed value parsers: no silent coercion."""

from decimal import Decimal

import pytest

from app.extraction import values
from app.extraction.values import AMBIGUOUS, EMPTY, OK, REJECTED


@pytest.mark.parametrize(
    ("raw", "status", "value"),
    [
        ("2430", OK, "2430"),
        ("| 836", OK, "836"),
        ("836 |", OK, "836"),
        ("( 2 )", OK, "2"),
        ("007", OK, "7"),
        ("0", OK, "0"),
        ("Lot", OK, "Lot"),
        ("SET", OK, "SET"),
        ("i", OK, "1"),
        ("O", OK, "0"),
        ("10 Nos", OK, "10"),
        ("25m", OK, "25"),
        # The review's table: every one of these was a plausible wrong number.
        ("1,250", OK, "1250"),
        ("12.5", REJECTED, None),
        ("-3", REJECTED, None),
        ("| -3", REJECTED, None),
        ("1 250", AMBIGUOUS, None),
        ("2 x 10", AMBIGUOUS, None),
        ("2x10", AMBIGUOUS, None),
        ("0.5", REJECTED, None),
        # OCR confusions and junk
        ("a 759", OK, "759"),           # a border mark read as a letter (EP-30784 EML)
        ("l 759", AMBIGUOUS, None),     # but "l" may be a 1
        ("a 7 5", AMBIGUOUS, None),
        ("ae", REJECTED, None),
        ("1O", AMBIGUOUS, None),
        ("3 7", AMBIGUOUS, None),
        ("12,5", REJECTED, None),
        ("1,25", REJECTED, None),
        ("£232", AMBIGUOUS, None),
        # overflow
        ("99999999", REJECTED, None),
        ("", EMPTY, None),
        ("  |  ", EMPTY, None),
        (None, EMPTY, None),
    ],
)
def test_parse_quantity(raw, status, value):
    parsed = values.parse_quantity(raw)
    assert (parsed.status, parsed.value) == (status, value), parsed.rule
    assert parsed.raw == raw
    assert parsed.rule


def test_space_grouping_only_where_the_column_allows_it():
    assert values.parse_quantity("1 250", allow_space_grouping=True).value == "1250"
    assert values.parse_quantity("12 50", allow_space_grouping=True).status == AMBIGUOUS


def test_unit_is_kept_apart_from_the_count():
    parsed = values.parse_quantity("10 Nos")
    assert parsed.unit == "Nos" and parsed.text() == "10"


def test_provenance_carries_the_rule_and_version():
    record = values.parse_quantity("1,250").to_dict()
    assert record == {"kind": "equipment_count", "raw": "1,250", "value": "1250", "status": OK,
                      "rule": "comma thousands grouping", "unit": None, "rules_version": values.PARSER_RULES_VERSION}


@pytest.mark.parametrize(
    ("raw", "status", "value"),
    [
        ("12.5", OK, Decimal("12.5")),
        ("1,234.50", OK, Decimal("1234.50")),
        ("12,5", AMBIGUOUS, None),
        ("-3.2", REJECTED, None),
        ("abc", REJECTED, None),
        ("", EMPTY, None),
    ],
)
def test_parse_decimal(raw, status, value):
    parsed = values.parse_decimal(raw)
    assert (parsed.status, parsed.value) == (status, value)


def test_negative_decimal_where_allowed():
    assert values.parse_decimal("-3.2", allow_negative=True).value == Decimal("-3.2")


@pytest.mark.parametrize(
    ("raw", "status", "value"),
    [("AED 1,250.00", OK, Decimal("1250.00")), ("12.345", REJECTED, None), ("-5", REJECTED, None), ("$ 40", OK, Decimal("40"))],
)
def test_parse_money(raw, status, value):
    parsed = values.parse_money(raw)
    assert (parsed.status, parsed.value) == (status, value)


def test_ratings_convert_units_and_say_so():
    current = values.parse_current("150 mA")
    assert current.ok and current.value == Decimal("0.15") and current.unit == "A" and "converted" in current.rule
    assert values.parse_current("0.5A").value == Decimal("0.5")
    assert values.parse_current("7 Ah").unit == "Ah"
    assert values.parse_current("0.5").status == AMBIGUOUS
    assert values.parse_current("0.5", default_unit="A").value == Decimal("0.5")
    assert values.parse_voltage("24 VDC").value == Decimal("24") and values.parse_voltage("24 VDC").unit == "V"
    assert values.parse_voltage("24 W").status == REJECTED
    assert values.parse_power("1.2 kW").value == Decimal("1200")


@pytest.mark.parametrize(("raw", "value"), [("R00", "R00"), ("Rev 1", "R01"), ("REV-A", "RA"), ("rev.02", "R02")])
def test_parse_revision(raw, value):
    assert values.parse_revision(raw).value == value


def test_revision_rejects_other_text():
    assert values.parse_revision("Revised drawing").status == REJECTED
