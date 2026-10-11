"""Tes isolasi pengguna untuk SETIAP fungsi service.

Pola: Alice punya data; Bob mencoba membaca/mengubah/memakai data Alice.
Bob harus ditolak (atau tidak melihat apa-apa), dan data serta saldo Alice
tidak berubah. Sebaliknya, operasi Alice tidak memengaruhi saldo Bob.
"""

import pytest

from database import queries
from models import STATUS_ACTIVE, STATUS_DELETED
from services import balances as bal
from services import transactions as svc
from services.adjustments import adjust_balance
from services.opening_balances import get_opening_balance, set_opening_balance
from services.transfers import add_transfer
from tests.services_layer.conftest import D
from utils.errors import NotFoundError, ValidationError


@pytest.fixture
def setup(session, alice, bob):
    """Alice dan Bob masing-masing punya saldo dan satu transaksi."""
    alice.open(alice.cash, 1_000_000)
    bob.open(bob.cash, 500_000)
    a_tx = svc.add_expense(
        session, alice.id, 100_000, alice.food, alice.cash, on=D, note="rahasia alice"
    ).transaction
    b_tx = svc.add_expense(session, bob.id, 50_000, bob.food, bob.cash, on=D).transaction
    session.commit()
    return a_tx, b_tx


def _alice_unchanged(session, alice):
    assert bal.get_total_balance(session, alice.id) == 900_000


# --- transactions --------------------------------------------------------------
def test_add_income_rejects_other_users_category_and_method(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        svc.add_income(session, bob.id, 1_000, alice.salary, bob.cash, on=D)
    with pytest.raises(ValidationError):
        svc.add_income(session, bob.id, 1_000, bob.salary, alice.cash, on=D)
    _alice_unchanged(session, alice)


def test_add_expense_rejects_other_users_category_and_method(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        svc.add_expense(session, bob.id, 1_000, alice.food, bob.cash, on=D)
    with pytest.raises(ValidationError):
        svc.add_expense(session, bob.id, 1_000, bob.food, alice.cash, on=D)
    _alice_unchanged(session, alice)


def test_get_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, bob.id, a_tx.id)
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, bob.id, a_tx.id, include_deleted=True)


def test_list_transactions_only_own(session, alice, bob, setup):
    _a_tx, b_tx = setup
    assert [t.id for t in svc.list_transactions(session, bob.id, include_deleted=True)] == [b_tx.id]
    # filter dengan id milik Alice / kata kunci Alice tidak membocorkan apa pun
    assert svc.list_transactions(session, bob.id, keyword="rahasia") == []
    assert svc.list_transactions(session, bob.id, category_id=alice.food) == []
    assert svc.list_transactions(session, bob.id, method_id=alice.cash) == []
    assert svc.list_transactions(session, 9999) == []


def test_edit_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.edit_transaction(session, bob.id, a_tx.id, amount=1)
    session.refresh(a_tx)
    assert a_tx.amount == 100_000
    _alice_unchanged(session, alice)


def test_edit_own_transaction_cannot_point_to_other_users_data(session, alice, bob, setup):
    _, b_tx = setup
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, b_tx.id, category_id=alice.food)
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, b_tx.id, method_id=alice.cash)
    bt = add_transfer(session, bob.id, 1_000, bob.cash, bob.bank, on=D).transfer
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, bt.id, to_method_id=alice.bank)
    _alice_unchanged(session, alice)


def test_delete_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_tx.id)
    session.refresh(a_tx)
    assert a_tx.status == STATUS_ACTIVE
    _alice_unchanged(session, alice)


def test_restore_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    svc.delete_transaction(session, alice.id, a_tx.id)
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_tx.id)
    session.refresh(a_tx)
    assert a_tx.status == STATUS_DELETED


# --- transfers -----------------------------------------------------------------
def test_transfer_rejects_other_users_methods(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        add_transfer(session, bob.id, 1_000, alice.cash, bob.bank, on=D)
    with pytest.raises(ValidationError):
        add_transfer(session, bob.id, 1_000, bob.cash, alice.bank, on=D)
    _alice_unchanged(session, alice)


def test_transfer_with_fee_only_affects_own_balances(session, alice, bob, setup):
    add_transfer(session, alice.id, 300_000, alice.cash, alice.bank, on=D, admin_fee=5_000)
    assert bal.get_total_balance(session, bob.id) == 450_000
    assert svc.list_transactions(session, bob.id, type_="transfer") == []


def test_delete_and_restore_transfer_with_fee_isolated_per_user(session, alice, bob, setup):
    """Operasi hapus dan pulihkan transfer berbiaya admin tidak bocor antar pengguna."""
    res = add_transfer(session, alice.id, 200_000, alice.cash, alice.bank, on=D, admin_fee=2_500)
    a_transfer = res.transfer
    a_fee = res.admin_fee

    # 1. Bob tidak bisa menghapus transfer atau biaya admin milik Alice
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_transfer.id)
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_fee.id)

    assert a_transfer.status == STATUS_ACTIVE
    assert a_fee.status == STATUS_ACTIVE
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 2. Alice menghapus transfernya: hanya transaksi Alice yang terhapus
    svc.delete_transaction(session, alice.id, a_transfer.id)
    session.refresh(a_transfer)
    session.refresh(a_fee)
    assert a_transfer.status == STATUS_DELETED
    assert a_fee.status == STATUS_DELETED
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 3. Bob tidak bisa memulihkan transfer atau biaya admin milik Alice
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_transfer.id)
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_fee.id)

    # 4. Alice memulihkan transfernya: hanya transaksi Alice yang dipulihkan
    svc.restore_transaction(session, alice.id, a_transfer.id)
    session.refresh(a_transfer)
    session.refresh(a_fee)
    assert a_transfer.status == STATUS_ACTIVE
    assert a_fee.status == STATUS_ACTIVE
    assert bal.get_total_balance(session, bob.id) == 450_000


# --- opening balances ----------------------------------------------------------
def test_set_opening_balance_on_other_users_method_rejected(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        set_opening_balance(session, bob.id, alice.cash, 1, D)
    assert get_opening_balance(session, alice.id, alice.cash).amount == 1_000_000
    _alice_unchanged(session, alice)


def test_get_opening_balance_of_other_user_is_none(session, alice, bob, setup):
    assert get_opening_balance(session, bob.id, alice.cash) is None


def test_set_opening_balance_does_not_affect_other_user(session, alice, bob, setup):
    set_opening_balance(session, alice.id, alice.bank, 7_000_000, D)
    assert bal.get_total_balance(session, bob.id) == 450_000


# --- adjustments ---------------------------------------------------------------
def test_adjust_balance_on_other_users_method_rejected(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        adjust_balance(session, bob.id, alice.cash, 0, on=D)
    _alice_unchanged(session, alice)


def test_adjust_balance_uses_only_own_transactions(session, alice, bob, setup):
    result = adjust_balance(session, bob.id, bob.cash, 450_000, on=D)
    assert result.previous_balance == 450_000
    assert result.transaction is None
    _alice_unchanged(session, alice)


# --- balances ------------------------------------------------------------------
def test_balance_services_are_per_user(session, alice, bob, setup):
    assert {b.method_id for b in bal.get_method_balances(session, bob.id)} == {
        bob.cash,
        bob.bank,
        bob.ewallet,
    }
    assert bal.get_total_balance(session, bob.id) == 450_000
    assert bal.get_total_balance(session, alice.id) == 900_000
    assert bal.get_method_balances(session, 9999) == []
    assert bal.get_total_balance(session, 9999) == 0


def test_get_method_balance_of_other_users_method_not_found(session, alice, bob, setup):
    with pytest.raises(NotFoundError):
        bal.get_method_balance(session, bob.id, alice.cash)


# --- pengguna tidak dikenal ----------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda s, a: svc.add_income(s, 9999, 1, a.salary, a.cash, on=D),
        lambda s, a: svc.add_expense(s, 9999, 1, a.food, a.cash, on=D),
        lambda s, a: add_transfer(s, 9999, 1, a.cash, a.bank, on=D),
        lambda s, a: set_opening_balance(s, 9999, a.cash, 1, D),
        lambda s, a: adjust_balance(s, 9999, a.cash, 1, on=D),
    ],
)
def test_writes_for_unknown_user_rejected(session, alice, setup, call):
    with pytest.raises(NotFoundError):
        call(session, alice)
    _alice_unchanged(session, alice)


def test_get_related_transactions_isolation(session, alice, bob):
    res = add_transfer(session, alice.id, 100_000, alice.cash, alice.bank, on=D, admin_fee=1_000)
    assert len(queries.get_related_transactions(session, alice.id, res.transfer.id)) == 1
    assert queries.get_related_transactions(session, bob.id, res.transfer.id) == []
    assert queries.get_related_transactions(session, 9999, res.transfer.id) == []
