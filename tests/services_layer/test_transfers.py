"""Tes service transfer dan biaya admin."""

import pytest

from database.seed import ADMIN_FEE_CATEGORY
from models import Category, Transaction
from services import balances as bal
from services import transactions as tx_svc
from services.transfers import add_transfer
from tests.services_layer.conftest import D
from utils.errors import ValidationError


def test_transfer_moves_money_without_changing_total(session, alice):
    alice.open(alice.bank, 1_000_000)
    alice.open(alice.cash, 100_000)
    result = add_transfer(session, alice.id, 300_000, alice.bank, alice.cash, on=D, note="tarik")

    t = result.transfer
    assert (t.type, t.amount, t.method_id, t.to_method_id) == (
        "transfer",
        300_000,
        alice.bank,
        alice.cash,
    )
    assert t.category_id is None and t.note == "tarik"
    assert result.admin_fee is None
    assert result.warnings == []
    assert bal.get_method_balance(session, alice.id, alice.bank) == 700_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 400_000
    assert bal.get_total_balance(session, alice.id) == 1_100_000


def test_transfer_is_not_income_or_expense(session, alice):
    alice.open(alice.bank, 1_000_000)
    add_transfer(session, alice.id, 300_000, alice.bank, alice.cash, on=D)
    assert tx_svc.list_transactions(session, alice.id, type_="income") == []
    assert tx_svc.list_transactions(session, alice.id, type_="expense") == []


def test_transfer_with_admin_fee(session, alice):
    alice.open(alice.bank, 1_000_000)
    result = add_transfer(
        session, alice.id, 300_000, alice.bank, alice.ewallet, on=D, admin_fee="2500"
    )

    fee = result.admin_fee
    assert fee is not None
    assert (fee.type, fee.amount, fee.method_id, fee.date) == ("expense", 2_500, alice.bank, D)
    assert fee.category_id == alice.admin_fee
    assert fee.to_method_id is None
    assert fee.related_transaction_id == result.transfer.id
    assert result.transfer.related_transaction_id is None
    # transfernya bukan pengeluaran; hanya biaya admin yang tercatat sebagai pengeluaran
    expenses = tx_svc.list_transactions(session, alice.id, type_="expense")
    assert [e.id for e in expenses] == [fee.id]
    assert bal.get_method_balance(session, alice.id, alice.bank) == 697_500
    assert bal.get_method_balance(session, alice.id, alice.ewallet) == 300_000
    assert bal.get_total_balance(session, alice.id) == 997_500


@pytest.mark.parametrize("fee", [None, 0, "0"])
def test_zero_or_missing_admin_fee_creates_no_expense(session, alice, fee):
    result = add_transfer(session, alice.id, 1_000, alice.bank, alice.cash, on=D, admin_fee=fee)
    assert result.admin_fee is None
    assert session.query(Transaction).count() == 1


@pytest.mark.parametrize("fee", [-1, 1.5, "abc", True])
def test_invalid_admin_fee_rejected(session, alice, fee):
    with pytest.raises(ValidationError):
        add_transfer(session, alice.id, 1_000, alice.bank, alice.cash, on=D, admin_fee=fee)
    assert session.query(Transaction).count() == 0


def test_admin_fee_category_created_when_missing(session, alice):
    session.query(Category).filter_by(id=alice.admin_fee).delete()
    session.commit()
    result = add_transfer(session, alice.id, 1_000, alice.bank, alice.cash, on=D, admin_fee=500)
    category = session.get(Category, result.admin_fee.category_id)
    assert (category.name, category.type, category.is_system, category.user_id) == (
        ADMIN_FEE_CATEGORY,
        "expense",
        True,
        alice.id,
    )


def test_admin_fee_uses_own_users_category(session, alice, bob):
    result = add_transfer(session, bob.id, 1_000, bob.bank, bob.cash, on=D, admin_fee=500)
    assert result.admin_fee.category_id == bob.admin_fee != alice.admin_fee


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": 0},
        {"amount": "-5"},
        {"to": "same"},
        {"to": None},
        {"on": "1-2-2026"},
        {"note": "n" * 256},
    ],
)
def test_invalid_transfer_saves_nothing(session, alice, kwargs):
    args = {"amount": 1_000, "from_method_id": alice.bank, "to_method_id": alice.cash, "on": D}
    if "to" in kwargs:
        to = kwargs.pop("to")
        args["to_method_id"] = alice.bank if to == "same" else to
    args.update(kwargs)
    with pytest.raises(ValidationError):
        add_transfer(session, alice.id, admin_fee=1_000, **args)
    assert session.query(Transaction).count() == 0


def test_transfer_with_archived_method_rejected(session, alice):
    alice.methods["E-Wallet"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError, match="diarsipkan"):
        add_transfer(session, alice.id, 1_000, alice.bank, alice.ewallet, on=D)
    with pytest.raises(ValidationError, match="diarsipkan"):
        add_transfer(session, alice.id, 1_000, alice.ewallet, alice.bank, on=D)


def test_soft_delete_and_restore_transfer_cascades_admin_fee(session, alice):
    """Soft delete dan restore transfer ikut menghapus dan memulihkan biaya adminnya."""
    alice.open(alice.bank, 1_000_000)
    res = add_transfer(session, alice.id, 200_000, alice.bank, alice.cash, on=D, admin_fee=2_500)
    transfer = res.transfer
    fee = res.admin_fee
    assert bal.get_method_balance(session, alice.id, alice.bank) == 797_500
    assert bal.get_method_balance(session, alice.id, alice.cash) == 200_000

    # 1. Soft delete transfer: transfer dan biaya adminnya ikut berstatus deleted
    del_res = tx_svc.delete_transaction(session, alice.id, transfer.id)
    assert del_res.transaction.status == "deleted"
    session.refresh(fee)
    assert fee.status == "deleted"
    # Saldo kembali seperti semula (transfer batal, biaya admin dibatalkan)
    assert bal.get_method_balance(session, alice.id, alice.bank) == 1_000_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 0

    # 2. Restore transfer: transfer dan biaya adminnya ikut berstatus active
    res_res = tx_svc.restore_transaction(session, alice.id, transfer.id)
    assert res_res.transaction.status == "active"
    session.refresh(fee)
    assert fee.status == "active"
    assert bal.get_method_balance(session, alice.id, alice.bank) == 797_500
    assert bal.get_method_balance(session, alice.id, alice.cash) == 200_000


def test_cannot_edit_or_delete_or_restore_linked_admin_fee_directly(session, alice):
    """Biaya admin yang terhubung tidak boleh diedit, dihapus, atau dipulihkan sendiri."""
    alice.open(alice.bank, 1_000_000)
    res = add_transfer(session, alice.id, 200_000, alice.bank, alice.cash, on=D, admin_fee=2_500)
    fee = res.admin_fee

    # Tolak edit langsung
    with pytest.raises(ValidationError, match="lewat transfernya"):
        tx_svc.edit_transaction(session, alice.id, fee.id, amount=5_000)

    # Tolak hapus langsung
    with pytest.raises(ValidationError, match="lewat transfernya"):
        tx_svc.delete_transaction(session, alice.id, fee.id)

    # Hapus transfer (keduanya terhapus)
    tx_svc.delete_transaction(session, alice.id, res.transfer.id)
    session.refresh(fee)
    assert fee.status == "deleted"

    # Tolak pulihkan biaya admin langsung
    with pytest.raises(ValidationError, match="lewat transfernya"):
        tx_svc.restore_transaction(session, alice.id, fee.id)


def test_edit_transfer_syncs_linked_admin_fee(session, alice):
    """Mengubah tanggal atau metode asal transfer ikut menyinkronkan biaya adminnya."""
    alice.open(alice.bank, 1_000_000)
    alice.open(alice.ewallet, 100_000)
    res = add_transfer(session, alice.id, 200_000, alice.bank, alice.cash, on=D, admin_fee=2_500)
    transfer = res.transfer
    fee = res.admin_fee

    # Ubah tanggal transfer dan metode asal ke E-Wallet
    new_date = "15-02-2026"
    tx_svc.edit_transaction(session, alice.id, transfer.id, on=new_date, method_id=alice.ewallet)
    session.refresh(fee)
    session.refresh(transfer)

    assert transfer.date == fee.date
    assert fee.method_id == alice.ewallet
    # Bank kembali 1.000.000 (tidak ada transfer/biaya dari Bank lagi)
    assert bal.get_method_balance(session, alice.id, alice.bank) == 1_000_000
    # E-Wallet berkurang: 100.000 - 200.000 - 2.500 = -102.500
    assert bal.get_method_balance(session, alice.id, alice.ewallet) == -102_500
