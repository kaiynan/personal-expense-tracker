"""Tes service transaksi: tambah, ambil, daftar, edit, soft delete, pulihkan."""

from datetime import date

import pytest

from models import STATUS_ACTIVE, STATUS_DELETED, Transaction
from services import balances as bal
from services import transactions as svc
from services.adjustments import adjust_balance
from services.transfers import add_transfer
from tests.services_layer.conftest import D
from utils.dates import today
from utils.errors import NotFoundError, ValidationError


def _count(session) -> int:
    return session.query(Transaction).count()


# --- Tambah --------------------------------------------------------------------
def test_brief_scenario_opening_income_expense(session, alice):
    """Saldo awal 1.000.000 + pemasukan 500.000 - pengeluaran 200.000 = 1.300.000."""
    alice.open(alice.bank, 1_000_000)
    svc.add_income(session, alice.id, 500_000, alice.salary, alice.bank, on=D)
    svc.add_expense(session, alice.id, 200_000, alice.food, alice.bank, on=D)
    assert bal.get_method_balance(session, alice.id, alice.bank) == 1_300_000
    assert bal.get_total_balance(session, alice.id) == 1_300_000


def test_add_income_stores_fields(session, alice):
    result = svc.add_income(
        session, alice.id, "750000", alice.salary, alice.cash, on="15-02-2026", note=" gaji "
    )
    tx = result.transaction
    assert tx.id is not None
    assert (tx.type, tx.amount, tx.date) == ("income", 750_000, date(2026, 2, 15))
    assert (tx.category_id, tx.method_id, tx.to_method_id) == (alice.salary, alice.cash, None)
    assert tx.note == "gaji"
    assert tx.status == STATUS_ACTIVE
    assert tx.user_id == alice.id
    assert result.warnings == []


def test_add_expense_default_date_is_today_in_user_timezone(session, alice):
    alice.open(alice.cash, 100_000)
    tx = svc.add_expense(session, alice.id, 10_000, alice.food, alice.cash).transaction
    assert tx.date == today(alice.user.timezone)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": 0},
        {"amount": -100},
        {"amount": 10.5},
        {"on": "2026-02-01"},
        {"note": "x" * 256},
        {"category_id": None},
        {"method_id": None},
        {"category_id": "salary"},  # kategori pemasukan untuk pengeluaran
    ],
)
def test_add_expense_invalid_input_saves_nothing(session, alice, kwargs):
    args = {"amount": 10_000, "category_id": alice.food, "method_id": alice.cash, "on": D}
    args.update(kwargs)
    if args["category_id"] == "salary":
        args["category_id"] = alice.salary
    with pytest.raises(ValidationError):
        svc.add_expense(session, alice.id, **args)
    assert _count(session) == 0


def test_add_with_archived_category_or_method_rejected(session, alice):
    alice.expense["Hiburan"].is_archived = True
    alice.methods["Bank"].is_archived = True
    session.commit()
    with pytest.raises(ValidationError):
        svc.add_expense(session, alice.id, 1_000, alice.expense["Hiburan"].id, alice.cash, on=D)
    with pytest.raises(ValidationError):
        svc.add_expense(session, alice.id, 1_000, alice.food, alice.bank, on=D)


def test_manual_expense_cannot_use_admin_fee_category(session, alice):
    with pytest.raises(ValidationError, match="sistem"):
        svc.add_expense(session, alice.id, 1_000, alice.admin_fee, alice.cash, on=D)


def test_unknown_user_rejected(session):
    with pytest.raises(NotFoundError):
        svc.add_income(session, 9999, 1_000, 1, 1, on=D)


# --- Ambil & daftar ------------------------------------------------------------
def test_get_transaction(session, alice):
    tx = svc.add_income(session, alice.id, 1_000, alice.salary, alice.cash, on=D).transaction
    assert svc.get_transaction(session, alice.id, tx.id) is tx
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, alice.id, 123_456)


def test_get_deleted_transaction_only_when_requested(session, alice):
    tx = svc.add_income(session, alice.id, 1_000, alice.salary, alice.cash, on=D).transaction
    svc.delete_transaction(session, alice.id, tx.id)
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, alice.id, tx.id)
    assert svc.get_transaction(session, alice.id, tx.id, include_deleted=True) is tx


@pytest.fixture
def history(session, alice):
    """Beberapa transaksi Alice untuk tes daftar/filter."""
    alice.open(alice.cash, 1_000_000)
    alice.open(alice.bank, 1_000_000)
    s, u = session, alice.id
    return {
        "salary": svc.add_income(
            s, u, 500_000, alice.salary, alice.bank, on=date(2026, 2, 1), note="Gaji Februari"
        ).transaction,
        "lunch": svc.add_expense(
            s, u, 25_000, alice.food, alice.cash, on=date(2026, 2, 3), note="Makan siang 50%_off"
        ).transaction,
        "bus": svc.add_expense(
            s, u, 5_000, alice.expense["Transportasi"].id, alice.cash, on=date(2026, 2, 5)
        ).transaction,
        "transfer": add_transfer(
            s, u, 100_000, alice.bank, alice.ewallet, on=date(2026, 2, 7)
        ).transfer,
        "deleted": svc.delete_transaction(
            s,
            u,
            svc.add_expense(
                s, u, 9_000, alice.food, alice.cash, on=date(2026, 2, 8)
            ).transaction.id,
        ).transaction,
    }


def _ids(rows):
    return [r.id for r in rows]


def test_list_default_active_only_newest_first(session, alice, history):
    h = history
    rows = svc.list_transactions(session, alice.id)
    assert _ids(rows) == [h["transfer"].id, h["bus"].id, h["lunch"].id, h["salary"].id]


def test_list_include_deleted(session, alice, history):
    rows = svc.list_transactions(session, alice.id, include_deleted=True)
    assert history["deleted"].id in _ids(rows)


def test_list_filters(session, alice, history):
    h, s, u = history, session, alice.id
    assert _ids(svc.list_transactions(s, u, type_="expense")) == [h["bus"].id, h["lunch"].id]
    assert _ids(svc.list_transactions(s, u, category_id=alice.food)) == [h["lunch"].id]
    # metode cocok sebagai asal maupun tujuan transfer
    assert _ids(svc.list_transactions(s, u, method_id=alice.ewallet)) == [h["transfer"].id]
    assert _ids(svc.list_transactions(s, u, method_id=alice.bank)) == [
        h["transfer"].id,
        h["salary"].id,
    ]
    assert _ids(svc.list_transactions(s, u, start="03-02-2026", end="05-02-2026")) == [
        h["bus"].id,
        h["lunch"].id,
    ]
    assert _ids(svc.list_transactions(s, u, limit=1)) == [h["transfer"].id]


def test_list_keyword_matches_note_or_category_case_insensitive(session, alice, history):
    h, s, u = history, session, alice.id
    assert _ids(svc.list_transactions(s, u, keyword="gaji")) == [h["salary"].id]
    assert _ids(svc.list_transactions(s, u, keyword="TRANSPORT")) == [h["bus"].id]
    # % dan _ dicari sebagai teks biasa, bukan wildcard
    assert _ids(svc.list_transactions(s, u, keyword="50%_")) == [h["lunch"].id]
    assert svc.list_transactions(s, u, keyword="%") == [h["lunch"]]
    assert svc.list_transactions(s, u, keyword="tidak-ada") == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type_": "hadiah"},
        {"start": "2026-02-01"},
        {"start": "10-02-2026", "end": "01-02-2026"},
        {"limit": 0},
        {"limit": True},
    ],
)
def test_list_invalid_filters(session, alice, kwargs):
    with pytest.raises(ValidationError):
        svc.list_transactions(session, alice.id, **kwargs)


# --- Edit ----------------------------------------------------------------------
def test_edit_updates_balance(session, alice):
    alice.open(alice.cash, 1_000_000)
    tx = svc.add_expense(session, alice.id, 200_000, alice.food, alice.cash, on=D).transaction
    svc.edit_transaction(session, alice.id, tx.id, amount=50_000)
    assert bal.get_method_balance(session, alice.id, alice.cash) == 950_000


def test_edit_move_to_other_method_updates_both(session, alice):
    alice.open(alice.cash, 1_000_000)
    alice.open(alice.bank, 1_000_000)
    tx = svc.add_expense(session, alice.id, 200_000, alice.food, alice.cash, on=D).transaction
    svc.edit_transaction(session, alice.id, tx.id, method_id=alice.bank)
    assert bal.get_method_balance(session, alice.id, alice.cash) == 1_000_000
    assert bal.get_method_balance(session, alice.id, alice.bank) == 800_000


def test_edit_fields(session, alice):
    tx = svc.add_expense(
        session, alice.id, 1_000, alice.food, alice.cash, on=D, note="lama"
    ).transaction
    shop = alice.expense["Belanja"].id
    svc.edit_transaction(session, alice.id, tx.id, on="10-02-2026", category_id=shop, note="baru")
    assert (tx.date, tx.category_id, tx.note) == (date(2026, 2, 10), shop, "baru")
    svc.edit_transaction(session, alice.id, tx.id, note="")
    assert tx.note is None


def test_edit_without_changes_is_noop(session, alice):
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    result = svc.edit_transaction(session, alice.id, tx.id, category_id=alice.food)
    assert result.transaction is tx
    assert result.warnings == []


def test_edit_keeps_working_when_current_category_archived(session, alice):
    fun = alice.expense["Hiburan"]
    tx = svc.add_expense(session, alice.id, 1_000, fun.id, alice.cash, on=D).transaction
    fun.is_archived = True
    session.commit()
    svc.edit_transaction(session, alice.id, tx.id, amount=2_000, category_id=fun.id)
    assert tx.amount == 2_000
    with pytest.raises(ValidationError):  # tetapi tidak bisa memilihnya sebagai kategori baru
        other = svc.add_expense(session, alice.id, 1, alice.food, alice.cash, on=D).transaction
        svc.edit_transaction(session, alice.id, other.id, category_id=fun.id)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": 0},
        {"on": "32-01-2026"},
        {"note": "x" * 300},
        {"category_id": "salary"},
        {"category_id": "admin"},
        {"to_method_id": "bank"},
        {"method_id": 999_999},
    ],
)
def test_edit_invalid_leaves_transaction_unchanged(session, alice, kwargs):
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    lookup = {"salary": alice.salary, "admin": alice.admin_fee, "bank": alice.bank}
    kwargs = {k: lookup.get(v, v) if isinstance(v, str) else v for k, v in kwargs.items()}
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, alice.id, tx.id, **kwargs)
    session.refresh(tx)
    assert (tx.amount, tx.date, tx.category_id, tx.method_id) == (1_000, D, alice.food, alice.cash)


def test_edit_transfer(session, alice):
    alice.open(alice.bank, 1_000_000)
    t = add_transfer(session, alice.id, 100_000, alice.bank, alice.cash, on=D).transfer
    svc.edit_transaction(session, alice.id, t.id, amount=300_000, to_method_id=alice.ewallet)
    assert bal.get_method_balance(session, alice.id, alice.bank) == 700_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 0
    assert bal.get_method_balance(session, alice.id, alice.ewallet) == 300_000
    assert bal.get_total_balance(session, alice.id) == 1_000_000


def test_edit_transfer_rules(session, alice):
    t = add_transfer(session, alice.id, 1_000, alice.bank, alice.cash, on=D).transfer
    with pytest.raises(ValidationError, match="tidak boleh sama"):
        svc.edit_transaction(session, alice.id, t.id, to_method_id=alice.bank)
    with pytest.raises(ValidationError, match="tidak boleh sama"):
        svc.edit_transaction(session, alice.id, t.id, method_id=alice.cash)
    with pytest.raises(ValidationError, match="kategori"):
        svc.edit_transaction(session, alice.id, t.id, category_id=alice.food)
    # tukar arah sekaligus boleh
    svc.edit_transaction(session, alice.id, t.id, method_id=alice.cash, to_method_id=alice.bank)
    assert (t.method_id, t.to_method_id) == (alice.cash, alice.bank)


def test_edit_adjustment_rejected(session, alice):
    adj = adjust_balance(session, alice.id, alice.cash, 50_000, on=D).transaction
    with pytest.raises(ValidationError, match="Koreksi"):
        svc.edit_transaction(session, alice.id, adj.id, note="x")


def test_edit_deleted_rejected(session, alice):
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    svc.delete_transaction(session, alice.id, tx.id)
    with pytest.raises(ValidationError, match="pulihkan"):
        svc.edit_transaction(session, alice.id, tx.id, amount=5)


def test_edit_missing_transaction(session, alice):
    with pytest.raises(NotFoundError):
        svc.edit_transaction(session, alice.id, 404, amount=5)


# --- Hapus & pulihkan ----------------------------------------------------------
def test_soft_delete_and_restore_update_balance(session, alice):
    alice.open(alice.cash, 1_000_000)
    tx = svc.add_expense(session, alice.id, 200_000, alice.food, alice.cash, on=D).transaction

    result = svc.delete_transaction(session, alice.id, tx.id)
    assert result.transaction.status == STATUS_DELETED
    assert bal.get_method_balance(session, alice.id, alice.cash) == 1_000_000
    assert _count(session) == 1  # baris tetap ada (soft delete)

    svc.restore_transaction(session, alice.id, tx.id)
    assert tx.status == STATUS_ACTIVE
    assert bal.get_method_balance(session, alice.id, alice.cash) == 800_000


def test_delete_twice_or_restore_active_rejected(session, alice):
    tx = svc.add_expense(session, alice.id, 1_000, alice.food, alice.cash, on=D).transaction
    with pytest.raises(ValidationError):
        svc.restore_transaction(session, alice.id, tx.id)
    svc.delete_transaction(session, alice.id, tx.id)
    with pytest.raises(ValidationError):
        svc.delete_transaction(session, alice.id, tx.id)


def test_delete_and_restore_missing(session, alice):
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, alice.id, 404)
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, alice.id, 404)


def test_delete_transfer_restores_both_methods(session, alice):
    alice.open(alice.bank, 1_000_000)
    t = add_transfer(session, alice.id, 400_000, alice.bank, alice.cash, on=D).transfer
    svc.delete_transaction(session, alice.id, t.id)
    assert bal.get_method_balance(session, alice.id, alice.bank) == 1_000_000
    assert bal.get_method_balance(session, alice.id, alice.cash) == 0
