"""Tes peringatan (tidak memblokir): saldo minus dan tanggal sebelum saldo awal."""

from datetime import date

from models import STATUS_ACTIVE
from services import balances as bal
from services import transactions as svc
from services.results import BEFORE_OPENING_DATE, NEGATIVE_BALANCE, warning_codes
from services.transfers import add_transfer
from tests.services_layer.conftest import D


# --- Saldo minus ---------------------------------------------------------------
def test_expense_making_balance_negative_warns_but_is_saved(session, alice):
    alice.open(alice.cash, 100_000)
    result = svc.add_expense(session, alice.id, 150_000, alice.food, alice.cash, on=D)

    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]
    warning = result.warnings[0]
    assert warning.method_id == alice.cash
    assert "Cash" in warning.message and "-Rp50.000" in warning.message
    session.commit()
    assert result.transaction.id is not None
    assert result.transaction.status == STATUS_ACTIVE
    assert bal.get_method_balance(session, alice.id, alice.cash) == -50_000


def test_no_warning_when_balance_stays_non_negative(session, alice):
    alice.open(alice.cash, 100_000)
    result = svc.add_expense(session, alice.id, 100_000, alice.food, alice.cash, on=D)
    assert result.warnings == []  # tepat nol bukan minus


def test_warning_when_already_negative_balance_goes_deeper(session, alice):
    svc.add_expense(session, alice.id, 10_000, alice.food, alice.cash, on=D)
    result = svc.add_expense(session, alice.id, 5_000, alice.food, alice.cash, on=D)
    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]


def test_income_into_negative_method_does_not_warn(session, alice):
    svc.add_expense(session, alice.id, 10_000, alice.food, alice.cash, on=D)
    result = svc.add_income(session, alice.id, 3_000, alice.salary, alice.cash, on=D)
    assert result.warnings == []  # masih minus, tetapi membaik


def test_transfer_making_source_negative_warns(session, alice):
    alice.open(alice.bank, 100_000)
    result = add_transfer(session, alice.id, 99_000, alice.bank, alice.cash, on=D, admin_fee=2_500)
    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]
    assert result.warnings[0].method_id == alice.bank
    assert bal.get_method_balance(session, alice.id, alice.bank) == -1_500


def test_edit_increasing_expense_warns(session, alice):
    alice.open(alice.cash, 100_000)
    tx = svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D).transaction
    result = svc.edit_transaction(session, alice.id, tx.id, amount=120_000)
    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]
    assert tx.amount == 120_000


def test_edit_moving_expense_to_empty_method_warns_for_new_method(session, alice):
    alice.open(alice.cash, 100_000)
    tx = svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D).transaction
    result = svc.edit_transaction(session, alice.id, tx.id, method_id=alice.ewallet)
    assert [(w.code, w.method_id) for w in result.warnings] == [(NEGATIVE_BALANCE, alice.ewallet)]


def test_deleting_income_that_makes_balance_negative_warns(session, alice):
    income = svc.add_income(session, alice.id, 100_000, alice.salary, alice.cash, on=D).transaction
    svc.add_expense(session, alice.id, 60_000, alice.food, alice.cash, on=D)
    result = svc.delete_transaction(session, alice.id, income.id)
    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]


def test_restoring_expense_that_makes_balance_negative_warns(session, alice):
    alice.open(alice.cash, 100_000)
    tx = svc.add_expense(session, alice.id, 150_000, alice.food, alice.cash, on=D).transaction
    svc.delete_transaction(session, alice.id, tx.id)
    result = svc.restore_transaction(session, alice.id, tx.id)
    assert warning_codes(result.warnings) == [NEGATIVE_BALANCE]


# --- Sebelum tanggal berlaku saldo awal ----------------------------------------
def test_transaction_before_opening_date_warns_and_does_not_change_balance(session, alice):
    alice.open(alice.cash, 100_000, effective=date(2026, 2, 1))
    result = svc.add_expense(session, alice.id, 500_000, alice.food, alice.cash, on="31-01-2026")
    assert warning_codes(result.warnings) == [BEFORE_OPENING_DATE]
    assert "31-01-2026" in result.warnings[0].message
    assert "01-02-2026" in result.warnings[0].message
    assert result.transaction.id is not None  # tetap tersimpan
    assert bal.get_method_balance(session, alice.id, alice.cash) == 100_000


def test_transaction_on_opening_date_has_no_warning(session, alice):
    alice.open(alice.cash, 100_000, effective=date(2026, 2, 1))
    result = svc.add_income(session, alice.id, 1_000, alice.salary, alice.cash, on="01-02-2026")
    assert result.warnings == []


def test_method_without_opening_balance_has_no_date_warning(session, alice):
    result = svc.add_income(session, alice.id, 1_000, alice.salary, alice.ewallet, on="01-01-2000")
    assert result.warnings == []


def test_transfer_before_opening_date_warns_per_method(session, alice):
    alice.open(alice.bank, 1_000_000, effective=date(2026, 1, 1))
    alice.open(alice.cash, 0, effective=date(2026, 3, 1))
    result = add_transfer(session, alice.id, 10_000, alice.bank, alice.cash, on=date(2026, 2, 1))
    assert [(w.code, w.method_id) for w in result.warnings] == [(BEFORE_OPENING_DATE, alice.cash)]


def test_edit_date_to_before_opening_warns(session, alice):
    alice.open(alice.cash, 100_000, effective=date(2026, 2, 1))
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    result = svc.edit_transaction(session, alice.id, tx.id, on="15-01-2026")
    assert warning_codes(result.warnings) == [BEFORE_OPENING_DATE]
    # edit keterangan saja tidak mengulang peringatan tanggal
    assert svc.edit_transaction(session, alice.id, tx.id, note="x").warnings == []


def test_restore_before_opening_warns(session, alice):
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    svc.delete_transaction(session, alice.id, tx.id)
    alice.open(alice.cash, 100_000, effective=date(2026, 3, 1))
    result = svc.restore_transaction(session, alice.id, tx.id)
    assert warning_codes(result.warnings) == [BEFORE_OPENING_DATE]
