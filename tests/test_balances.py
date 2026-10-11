"""Tes perhitungan saldo (query agregat di database/balances.py)."""

from datetime import date

import pytest

from database.balances import compute_balance, method_balances, total_balance


# --- fungsi murni ----------------------------------------------------------------
def test_compute_balance_formula():
    assert compute_balance(1_000_000, 500_000, 200_000, 0, 0, 0) == 1_300_000


def test_compute_balance_with_transfers_and_adjustment():
    # 100 + 50 - 30 + 20 - 10 + (-5) = 125
    assert compute_balance(100, 50, 30, 20, 10, -5) == 125


def test_compute_balance_can_be_negative():
    assert compute_balance(0, 0, 10, 0, 0, 0) == -10


# --- skenario di brief -----------------------------------------------------------
def test_opening_plus_income_minus_expense(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000)
    add_tx(user, "income", 500_000, cash)
    add_tx(user, "expense", 200_000, cash)

    assert method_balances(session, user.id)[cash.id] == 1_300_000
    assert total_balance(session, user.id) == 1_300_000


def test_transfer_moves_money_but_keeps_total(session, make_user, make_method, add_tx):
    user = make_user()
    bank = make_method(user, "Bank", opening=2_000_000)
    cash = make_method(user, "Cash", opening=1_000_000)
    total_before = total_balance(session, user.id)

    add_tx(user, "transfer", 300_000, bank, to=cash)

    balances = method_balances(session, user.id)
    assert balances[bank.id] == 1_700_000
    assert balances[cash.id] == 1_300_000
    assert total_balance(session, user.id) == total_before == 3_000_000


def test_transfer_with_admin_fee_reduces_total_only_by_fee(session, make_user, make_method, add_tx):
    user = make_user()
    bank = make_method(user, "Bank", opening=2_000_000)
    cash = make_method(user, "Cash", opening=0)

    add_tx(user, "transfer", 300_000, bank, to=cash)
    add_tx(user, "expense", 2_500, bank)  # biaya admin = pengeluaran terpisah

    balances = method_balances(session, user.id)
    assert balances[bank.id] == 2_000_000 - 300_000 - 2_500
    assert balances[cash.id] == 300_000
    assert total_balance(session, user.id) == 2_000_000 - 2_500


def test_soft_deleted_transaction_is_ignored(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000)
    add_tx(user, "expense", 200_000, cash)
    add_tx(user, "expense", 999_999, cash, status="deleted")

    assert method_balances(session, user.id)[cash.id] == 800_000


def test_deleted_transfer_is_ignored_on_both_sides(session, make_user, make_method, add_tx):
    user = make_user()
    bank = make_method(user, "Bank", opening=1_000)
    cash = make_method(user, "Cash", opening=0)
    add_tx(user, "transfer", 400, bank, to=cash, status="deleted")

    balances = method_balances(session, user.id)
    assert balances[bank.id] == 1_000
    assert balances[cash.id] == 0


def test_edit_updates_balance(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000)
    tx = add_tx(user, "expense", 200_000, cash)

    tx.amount = 250_000
    session.commit()

    assert method_balances(session, user.id)[cash.id] == 750_000


@pytest.mark.parametrize("adjustment, expected", [(50_000, 1_050_000), (-70_000, 930_000)])
def test_adjustment_affects_balance(session, make_user, make_method, add_tx, adjustment, expected):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000)
    add_tx(user, "adjustment", adjustment, cash)

    assert method_balances(session, user.id)[cash.id] == expected


def test_balance_can_go_negative(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=100_000)
    add_tx(user, "expense", 150_000, cash)

    assert method_balances(session, user.id)[cash.id] == -50_000


# --- saldo awal & tanggal berlaku ---------------------------------------------------
def test_transactions_before_effective_date_are_not_counted(
    session, make_user, make_method, add_tx
):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000, effective=date(2026, 2, 1))
    add_tx(user, "income", 777_000, cash, on=date(2026, 1, 15))  # sebelum berlaku
    add_tx(user, "income", 100_000, cash, on=date(2026, 2, 1))  # tepat hari berlaku

    assert method_balances(session, user.id)[cash.id] == 1_100_000


def test_method_without_opening_counts_everything_from_zero(
    session, make_user, make_method, add_tx
):
    user = make_user()
    cash = make_method(user, "Cash")
    add_tx(user, "income", 300_000, cash, on=date(2020, 1, 1))
    add_tx(user, "expense", 100_000, cash, on=date(2026, 2, 1))

    assert method_balances(session, user.id)[cash.id] == 200_000


def test_method_without_transactions_shows_opening_or_zero(session, make_user, make_method):
    user = make_user()
    with_opening = make_method(user, "Bank", opening=500_000)
    empty = make_method(user, "E-Wallet")

    balances = method_balances(session, user.id)
    assert balances[with_opening.id] == 500_000
    assert balances[empty.id] == 0


def test_archived_method_still_counts_in_total(session, make_user, make_method):
    user = make_user()
    make_method(user, "Cash", opening=100_000)
    make_method(user, "Lama", opening=50_000, archived=True)

    assert total_balance(session, user.id) == 150_000


# --- as_of ---------------------------------------------------------------------------
def test_as_of_limits_to_date(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000, effective=date(2026, 1, 1))
    add_tx(user, "income", 100_000, cash, on=date(2026, 2, 10))
    add_tx(user, "income", 200_000, cash, on=date(2026, 3, 10))

    assert method_balances(session, user.id, as_of=date(2026, 2, 28))[cash.id] == 1_100_000
    assert method_balances(session, user.id, as_of=date(2026, 3, 10))[cash.id] == 1_300_000
    assert total_balance(session, user.id) == 1_300_000


def test_as_of_before_opening_effective_date_gives_zero(session, make_user, make_method, add_tx):
    user = make_user()
    cash = make_method(user, "Cash", opening=1_000_000, effective=date(2026, 2, 1))
    add_tx(user, "income", 100_000, cash, on=date(2026, 2, 5))

    assert method_balances(session, user.id, as_of=date(2026, 1, 31))[cash.id] == 0


# --- tipe hasil --------------------------------------------------------------------------
def test_results_are_plain_integers(session, make_user, make_method, add_tx):
    user = make_user()
    bank = make_method(user, "Bank", opening=1_000)
    cash = make_method(user, "Cash")
    add_tx(user, "income", 500, bank)
    add_tx(user, "transfer", 100, bank, to=cash)

    balances = method_balances(session, user.id)
    assert all(type(value) is int for value in balances.values())
    assert type(total_balance(session, user.id)) is int
