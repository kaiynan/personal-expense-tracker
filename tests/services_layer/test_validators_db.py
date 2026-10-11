"""Tes validasi kategori dan metode (butuh database)."""

import pytest

from models import TYPE_EXPENSE, TYPE_INCOME
from utils.errors import ValidationError
from utils.validators import validate_category, validate_method


def test_method_own_and_active_is_valid(session, alice):
    assert validate_method(session, alice.id, alice.cash).id == alice.cash


def test_method_of_other_user_rejected(session, alice, bob):
    with pytest.raises(ValidationError, match="tidak ditemukan"):
        validate_method(session, alice.id, bob.cash)


@pytest.mark.parametrize("bad", [None, 999_999, "1", True, 1.0])
def test_method_missing_or_wrong_type_rejected(session, alice, bad):
    with pytest.raises(ValidationError):
        validate_method(session, alice.id, bad)


def test_archived_method_rejected(session, alice):
    alice.methods["Bank"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError, match="diarsipkan"):
        validate_method(session, alice.id, alice.bank)


def test_category_own_and_active_is_valid(session, alice):
    assert validate_category(session, alice.id, alice.food, TYPE_EXPENSE).id == alice.food
    assert validate_category(session, alice.id, alice.salary, TYPE_INCOME).id == alice.salary


def test_category_of_other_user_rejected(session, alice, bob):
    with pytest.raises(ValidationError, match="tidak ditemukan"):
        validate_category(session, alice.id, bob.food, TYPE_EXPENSE)


@pytest.mark.parametrize("bad", [None, 999_999, "1", False])
def test_category_missing_or_wrong_type_rejected(session, alice, bad):
    with pytest.raises(ValidationError):
        validate_category(session, alice.id, bad, TYPE_EXPENSE)


def test_archived_category_rejected(session, alice):
    alice.expense["Hiburan"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError, match="diarsipkan"):
        validate_category(session, alice.id, alice.expense["Hiburan"].id, TYPE_EXPENSE)


def test_category_type_must_match(session, alice):
    with pytest.raises(ValidationError, match="bukan kategori"):
        validate_category(session, alice.id, alice.salary, TYPE_EXPENSE)
    with pytest.raises(ValidationError, match="bukan kategori"):
        validate_category(session, alice.id, alice.food, TYPE_INCOME)


def test_system_category_only_when_allowed(session, alice):
    with pytest.raises(ValidationError, match="sistem"):
        validate_category(session, alice.id, alice.admin_fee, TYPE_EXPENSE)
    category = validate_category(
        session, alice.id, alice.admin_fee, TYPE_EXPENSE, allow_system=True
    )
    assert category.id == alice.admin_fee
