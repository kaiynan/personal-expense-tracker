"""Tes untuk layer service aliases dan parse_quick_input."""

import pytest

from models import TARGET_CATEGORY, TARGET_PAYMENT_METHOD, TYPE_EXPENSE, TYPE_INCOME
from services.aliases import (
    add_alias,
    delete_alias,
    get_alias,
    list_aliases,
    parse_quick_input,
    seed_default_aliases,
)
from utils.errors import NotFoundError
from utils.validators import ValidationError


def test_add_and_get_alias(session, alice):
    alias = add_alias(session, alice.id, "warung", TARGET_CATEGORY, alice.food)
    assert alias.id is not None
    assert alias.alias == "warung"
    assert alias.target_type == TARGET_CATEGORY
    assert alias.category_id == alice.food

    fetched = get_alias(session, alice.id, alias.id)
    assert fetched.id == alias.id
    assert fetched.alias == "warung"


def test_add_method_alias(session, alice):
    alias = add_alias(session, alice.id, "kantong", TARGET_PAYMENT_METHOD, alice.cash)
    assert alias.target_type == TARGET_PAYMENT_METHOD
    assert alias.method_id == alice.cash


def test_add_alias_duplicate_fails(session, alice):
    add_alias(session, alice.id, "warung", TARGET_CATEGORY, alice.food)
    with pytest.raises(ValidationError, match="sudah digunakan"):
        add_alias(session, alice.id, "warung", TARGET_PAYMENT_METHOD, alice.cash)


def test_add_alias_blank_fails(session, alice):
    with pytest.raises(ValidationError, match="tidak boleh kosong"):
        add_alias(session, alice.id, "   ", TARGET_CATEGORY, alice.food)


def test_add_alias_invalid_target_type(session, alice):
    with pytest.raises(ValidationError, match="tidak valid"):
        add_alias(session, alice.id, "test", "other_type", alice.food)


def test_add_alias_target_not_found(session, alice):
    with pytest.raises(NotFoundError, match="tidak ditemukan"):
        add_alias(session, alice.id, "test_cat", TARGET_CATEGORY, 99999)

    with pytest.raises(NotFoundError, match="tidak ditemukan"):
        add_alias(session, alice.id, "test_met", TARGET_PAYMENT_METHOD, 99999)


def test_delete_alias(session, alice):
    alias = add_alias(session, alice.id, "warung", TARGET_CATEGORY, alice.food)
    assert delete_alias(session, alice.id, alias.id) is True

    with pytest.raises(NotFoundError):
        get_alias(session, alice.id, alias.id)

    with pytest.raises(NotFoundError):
        delete_alias(session, alice.id, alias.id)


def test_list_aliases_filtering(session, alice):
    cat_aliases = list_aliases(session, alice.id, target_type=TARGET_CATEGORY)
    met_aliases = list_aliases(session, alice.id, target_type=TARGET_PAYMENT_METHOD)
    all_aliases = list_aliases(session, alice.id)

    assert len(cat_aliases) > 0
    assert len(met_aliases) > 0
    assert len(all_aliases) == len(cat_aliases) + len(met_aliases)


def test_seed_default_aliases_idempotent(session, alice):
    count_before = len(list_aliases(session, alice.id))
    # Running again shouldn't duplicate or crash
    seeded_count = seed_default_aliases(session, alice.id)
    assert seeded_count == 0  # no new ones created
    assert len(list_aliases(session, alice.id)) == count_before


# --- Quick input parsing ---


def test_parse_quick_input_basic_expense(session, alice):
    # 'makan 15000 gopay' -> e-wallet is default seeded alias 'gopay'
    res = parse_quick_input(session, alice.id, "makan 15000 gopay")
    assert res.amount == 15000
    assert res.type == TYPE_EXPENSE
    assert res.category_id == alice.food
    assert res.method_id == alice.ewallet
    assert res.category_name == "Makanan"
    assert res.method_name == "E-Wallet"


def test_parse_quick_input_with_shorthand_amount_and_note(session, alice):
    # 'bensin 25k cash untuk motor beat'
    res = parse_quick_input(session, alice.id, "bensin 25k cash untuk motor beat")
    assert res.amount == 25000
    assert res.type == TYPE_EXPENSE
    assert res.category_id == alice.expense["Transportasi"].id
    assert res.method_id == alice.cash
    assert res.note == "untuk motor beat"


def test_parse_quick_input_with_jutaan_and_income(session, alice):
    # '1.5jt gaji bca'
    res = parse_quick_input(session, alice.id, "1.5jt gaji bca")
    assert res.amount == 1500000
    assert res.type == TYPE_INCOME
    assert res.category_id == alice.salary
    assert res.method_id == alice.bank


def test_parse_quick_input_default_payment_method(session, alice):
    # If method omitted, defaults to Cash
    res = parse_quick_input(session, alice.id, "kopi 15rb")
    assert res.amount == 15000
    assert res.type == TYPE_EXPENSE
    assert res.category_id == alice.food
    assert res.method_id == alice.cash


def test_parse_quick_input_errors(session, alice):
    # Empty text
    with pytest.raises(ValidationError, match="tidak boleh kosong"):
        parse_quick_input(session, alice.id, "   ")

    # Missing amount
    with pytest.raises(ValidationError, match="Nominal transaksi tidak ditemukan"):
        parse_quick_input(session, alice.id, "makan bakso")

    # Zero or negative amount
    with pytest.raises(ValidationError, match="Nominal harus lebih dari 0"):
        parse_quick_input(session, alice.id, "makan 0 cash")

    # Missing category
    with pytest.raises(ValidationError, match="Kategori tidak dikenali"):
        parse_quick_input(session, alice.id, "50000 cash uang jajan")
