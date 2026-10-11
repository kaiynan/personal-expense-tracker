"""Tes service saldo awal, koreksi saldo (adjustment), dan layanan saldo."""

from datetime import date

import pytest

from models import OpeningBalance
from services import balances as bal
from services import transactions as tx_svc
from services.adjustments import adjust_balance
from services.opening_balances import get_opening_balance, set_opening_balance
from services.transfers import add_transfer
from tests.services_layer.conftest import D
from utils.dates import today
from utils.errors import NotFoundError, ValidationError


# --- Saldo awal ----------------------------------------------------------------
def test_set_and_get_opening_balance(session, alice):
    result = set_opening_balance(session, alice.id, alice.bank, "1000000", "01-01-2026")
    ob = result.opening_balance
    assert (ob.user_id, ob.method_id, ob.amount, ob.effective_date) == (
        alice.id,
        alice.bank,
        1_000_000,
        date(2026, 1, 1),
    )
    assert result.warnings == []
    assert get_opening_balance(session, alice.id, alice.bank) is ob
    assert get_opening_balance(session, alice.id, alice.cash) is None
    assert bal.get_method_balance(session, alice.id, alice.bank) == 1_000_000


def test_set_opening_balance_twice_updates_single_row(session, alice):
    set_opening_balance(session, alice.id, alice.bank, 1_000_000, date(2026, 1, 1))
    set_opening_balance(session, alice.id, alice.bank, 250_000, date(2026, 1, 15))
    rows = session.query(OpeningBalance).filter_by(user_id=alice.id).all()
    assert len(rows) == 1
    assert (rows[0].amount, rows[0].effective_date) == (250_000, date(2026, 1, 15))


def test_opening_balance_default_date_and_zero_allowed(session, alice):
    ob = set_opening_balance(session, alice.id, alice.cash, 0).opening_balance
    assert ob.amount == 0
    assert ob.effective_date == today(alice.user.timezone)


@pytest.mark.parametrize("amount", [-1, 1.5, "1.000", None])
def test_opening_balance_invalid_amount(session, alice, amount):
    with pytest.raises(ValidationError):
        set_opening_balance(session, alice.id, alice.cash, amount, D)


def test_opening_balance_archived_method_rejected(session, alice):
    alice.methods["Cash"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError):
        set_opening_balance(session, alice.id, alice.cash, 1_000, D)


def test_lowering_opening_balance_warns_negative(session, alice):
    alice.open(alice.cash, 100_000)
    tx_svc.add_expense(session, alice.id, 80_000, alice.food, alice.cash, on=D)
    result = set_opening_balance(session, alice.id, alice.cash, 50_000, date(2026, 1, 1))
    assert [w.code for w in result.warnings] == ["negative_balance"]


# --- Koreksi saldo -------------------------------------------------------------
def test_adjustment_up_and_down(session, alice):
    alice.open(alice.cash, 100_000)
    up = adjust_balance(session, alice.id, alice.cash, 130_000, on=D)
    assert (up.previous_balance, up.actual_balance, up.difference) == (100_000, 130_000, 30_000)
    assert (up.transaction.type, up.transaction.amount) == ("adjustment", 30_000)
    assert up.transaction.category_id is None

    down = adjust_balance(session, alice.id, alice.cash, "90000", on=D)
    assert down.difference == -40_000
    assert down.transaction.amount == -40_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 90_000


def test_adjustment_with_zero_difference_creates_nothing(session, alice):
    alice.open(alice.cash, 100_000)
    result = adjust_balance(session, alice.id, alice.cash, 100_000, on=D)
    assert result.transaction is None
    assert result.difference == 0
    assert tx_svc.list_transactions(session, alice.id) == []


def test_adjustment_to_zero(session, alice):
    alice.open(alice.cash, 100_000)
    adjust_balance(session, alice.id, alice.cash, 0, on=D)
    assert bal.get_method_balance(session, alice.id, alice.cash) == 0


def test_adjustment_counts_against_balance_on_its_date(session, alice):
    """Transaksi setelah tanggal koreksi tidak ikut menentukan selisih."""
    alice.open(alice.cash, 100_000)
    tx_svc.add_expense(session, alice.id, 30_000, alice.food, alice.cash, on=date(2026, 3, 1))
    result = adjust_balance(session, alice.id, alice.cash, 120_000, on=date(2026, 2, 1))
    assert result.previous_balance == 100_000
    assert result.difference == 20_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 90_000


def test_adjustment_affects_balance_but_is_not_income_or_expense(session, alice):
    alice.open(alice.cash, 100_000)
    adjust_balance(session, alice.id, alice.cash, 150_000, on=D)
    adjust_balance(session, alice.id, alice.cash, 20_000, on=D)
    assert bal.get_total_balance(session, alice.id) == 20_000
    assert tx_svc.list_transactions(session, alice.id, type_="income") == []
    assert tx_svc.list_transactions(session, alice.id, type_="expense") == []


def test_adjustment_before_opening_date_rejected(session, alice):
    alice.open(alice.cash, 100_000, effective=date(2026, 2, 1))
    with pytest.raises(ValidationError, match="saldo awal"):
        adjust_balance(session, alice.id, alice.cash, 50_000, on=date(2026, 1, 31))
    assert tx_svc.list_transactions(session, alice.id) == []


def test_adjustment_without_opening_balance(session, alice):
    result = adjust_balance(session, alice.id, alice.ewallet, 75_000, on=D, note="cek app")
    assert result.difference == 75_000
    assert result.transaction.note == "cek app"


def test_adjustment_warns_when_later_transactions_keep_balance_negative(session, alice):
    alice.open(alice.cash, 100_000)
    tx_svc.add_expense(session, alice.id, 150_000, alice.food, alice.cash, on=date(2026, 3, 1))
    result = adjust_balance(session, alice.id, alice.cash, 20_000, on=date(2026, 2, 1))
    assert [w.code for w in result.warnings] == ["negative_balance"]


@pytest.mark.parametrize("actual", [-1, 2.5, "abc", None])
def test_adjustment_invalid_actual(session, alice, actual):
    with pytest.raises(ValidationError):
        adjust_balance(session, alice.id, alice.cash, actual, on=D)


def test_adjustment_archived_method_rejected(session, alice):
    alice.methods["Cash"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError):
        adjust_balance(session, alice.id, alice.cash, 1, on=D)


# --- Layanan saldo -------------------------------------------------------------
def test_method_balances_listing(session, alice):
    alice.open(alice.cash, 100_000)
    alice.open(alice.bank, 1_000_000)
    add_transfer(session, alice.id, 200_000, alice.bank, alice.ewallet, on=D)
    alice.methods["E-Wallet"].is_archived = True
    session.commit()

    rows = {b.name: b for b in bal.get_method_balances(session, alice.id)}
    assert {n: b.balance for n, b in rows.items()} == {
        "Cash": 100_000,
        "Bank": 800_000,
        "E-Wallet": 200_000,
    }
    assert rows["E-Wallet"].is_archived is True
    assert bal.get_total_balance(session, alice.id) == 1_100_000


def test_balances_as_of_date(session, alice):
    alice.open(alice.cash, 100_000)
    tx_svc.add_income(session, alice.id, 50_000, alice.salary, alice.cash, on=date(2026, 3, 1))
    assert bal.get_total_balance(session, alice.id, as_of="28-02-2026") == 100_000
    assert bal.get_total_balance(session, alice.id, as_of=date(2026, 3, 1)) == 150_000
    assert bal.get_method_balance(session, alice.id, alice.cash, as_of="28-02-2026") == 100_000
    rows = bal.get_method_balances(session, alice.id, as_of="28-02-2026")
    assert sum(r.balance for r in rows) == 100_000


def test_balance_invalid_inputs(session, alice):
    with pytest.raises(ValidationError):
        bal.get_total_balance(session, alice.id, as_of="2026-02-28")
    with pytest.raises(NotFoundError):
        bal.get_method_balance(session, alice.id, 999_999)
