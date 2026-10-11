"""Tes validasi murni di utils/ (tanpa database)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

import config
from models import Transaction
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date, validate_amount, validate_balance, validate_note


# --- Nominal -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), (15_000, 15_000), ("15000", 15_000), ("  2500 ", 2_500)],
)
def test_amount_valid(value, expected):
    assert validate_amount(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        "0",
        "-5",
        "+5",
        15000.0,
        1.5,
        True,
        False,
        None,
        "",
        "abc",
        "1.000",
        "1,5",
        "١٢٣",
        Decimal("100"),
    ],
)
def test_amount_invalid(value):
    with pytest.raises(ValidationError):
        validate_amount(value)


def test_amount_max_boundary():
    assert validate_amount(config.MAX_AMOUNT) == config.MAX_AMOUNT
    with pytest.raises(ValidationError, match="maksimum"):
        validate_amount(config.MAX_AMOUNT + 1)


def test_amount_max_read_from_config(monkeypatch):
    monkeypatch.setattr(config, "MAX_AMOUNT", 100)
    assert validate_amount(100) == 100
    with pytest.raises(ValidationError):
        validate_amount(101)


def test_balance_allows_zero_but_not_negative():
    assert validate_balance(0) == 0
    assert validate_balance("0") == 0
    with pytest.raises(ValidationError):
        validate_balance(-1)
    with pytest.raises(ValidationError):
        validate_balance(config.MAX_AMOUNT + 1)


# --- Tanggal -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01-02-2026", date(2026, 2, 1)),
        (" 31-12-2025 ", date(2025, 12, 31)),
        ("29-02-2028", date(2028, 2, 29)),  # tahun kabisat
        (date(2026, 3, 4), date(2026, 3, 4)),
    ],
)
def test_date_valid(value, expected):
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-01",
        "1-2-2026",
        "01/02/2026",
        "31-02-2026",
        "29-02-2026",
        "00-01-2026",
        "01-13-2026",
        "",
        "kemarin",
        None,
        20260201,
        datetime(2026, 2, 1, 10, 0),
    ],
)
def test_date_invalid(value):
    with pytest.raises(ValidationError):
        parse_date(value)


# --- Keterangan ----------------------------------------------------------------
def test_note_optional_and_trimmed():
    assert validate_note(None) is None
    assert validate_note("") is None
    assert validate_note("   ") is None
    assert validate_note("  makan siang ") == "makan siang"


def test_note_length_limit():
    assert validate_note("a" * config.MAX_NOTE_LENGTH) == "a" * config.MAX_NOTE_LENGTH
    with pytest.raises(ValidationError, match="maksimal"):
        validate_note("a" * (config.MAX_NOTE_LENGTH + 1))


def test_note_length_counted_after_trim():
    assert validate_note(" " + "a" * config.MAX_NOTE_LENGTH + " ")


def test_note_must_be_text():
    with pytest.raises(ValidationError):
        validate_note(123)


def test_note_limit_fits_column():
    assert Transaction.__table__.c.note.type.length >= config.MAX_NOTE_LENGTH


# --- Format --------------------------------------------------------------------
def test_format_rupiah():
    assert format_rupiah(1_500_000) == "Rp1.500.000"
    assert format_rupiah(0) == "Rp0"
    assert format_rupiah(-50_000) == "-Rp50.000"
