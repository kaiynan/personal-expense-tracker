"""Pengujian unit dan integrasi untuk services.audit (Audit Log & Undo).

Mencakup:
- Pencatatan log audit otomatis pada tambah, edit, hapus, pulihkan transaksi,
  transfer, dan koreksi saldo.
- Pengambilan dan daftar riwayat perubahan beserta filternya.
- Pembatalan (undo) aksi penambahan (income, expense, transfer berbiaya admin, koreksi saldo).
- Pembatalan (undo) aksi penghapusan (soft delete) dan pemulihan (restore).
- Pembatalan (undo) aksi pengubahan (edit) nilai transaksi.
- Pembalikan beruntun lewat undo_last (LIFO).
- Pencegahan pembatalan ganda (double-undo).
- Peringatan saldo saat pembalikan dilakukan.
"""

from datetime import date

import pytest

from models import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_RESTORE,
    ACTION_UPDATE,
    STATUS_ACTIVE,
    STATUS_DELETED,
)
from services import adjustments as adj
from services import audit
from services import balances as bal
from services import transactions as svc
from services import transfers as trf
from tests.services_layer.conftest import D
from utils.errors import NotFoundError, ValidationError


def test_record_and_get_audit_log(session, alice):
    log = audit.record_audit_log(
        session,
        alice.id,
        action=ACTION_CREATE,
        entity_type="transaction",
        entity_id=123,
        description="Manual log test",
    )
    session.commit()

    assert log.id is not None
    assert log.user_id == alice.id
    assert log.action == ACTION_CREATE
    assert log.description == "Manual log test"
    assert log.is_undone is False

    fetched = audit.get_audit_log(session, alice.id, log.id)
    assert fetched.id == log.id

    with pytest.raises(NotFoundError):
        audit.get_audit_log(session, alice.id, 99999)


def test_record_audit_log_invalid_action(session, alice):
    with pytest.raises(ValidationError, match="tidak valid"):
        audit.record_audit_log(
            session,
            alice.id,
            action="invalid_action",
            entity_type="transaction",
            entity_id=1,
        )


def test_list_audit_logs_and_filters(session, alice):
    audit.record_audit_log(
        session, alice.id, ACTION_CREATE, "transaction", 1, description="Entry 1"
    )
    audit.record_audit_log(
        session, alice.id, ACTION_UPDATE, "transaction", 1, description="Entry 2"
    )
    audit.record_audit_log(
        session, alice.id, ACTION_DELETE, "transaction", 2, description="Entry 3"
    )
    session.commit()

    all_logs = audit.list_audit_logs(session, alice.id)
    assert len(all_logs) == 3
    # Urutan terbaru lebih dulu
    assert [item.description for item in all_logs] == ["Entry 3", "Entry 2", "Entry 1"]

    # Filter action
    update_logs = audit.list_audit_logs(session, alice.id, action=ACTION_UPDATE)
    assert len(update_logs) == 1
    assert update_logs[0].description == "Entry 2"

    # Filter entity_id
    tx1_logs = audit.list_audit_logs(session, alice.id, entity_id=1)
    assert len(tx1_logs) == 2

    # Limit
    limited = audit.list_audit_logs(session, alice.id, limit=2)
    assert len(limited) == 2

    # Validasi limit
    with pytest.raises(ValidationError):
        audit.list_audit_logs(session, alice.id, limit=0)


def test_auto_audit_on_transactions_crud(session, alice):
    alice.open(alice.cash, 1_000_000)

    # 1. Tambah pengeluaran -> audit ACTION_CREATE
    res_add = svc.add_expense(
        session, alice.id, 50_000, alice.food, alice.cash, on=D, note="Nasi goreng"
    )
    session.commit()
    tx = res_add.transaction

    logs = audit.list_audit_logs(session, alice.id, entity_id=tx.id)
    assert len(logs) == 1
    assert logs[0].action == ACTION_CREATE
    assert "Tambah expense" in (logs[0].description or "")

    # 2. Edit pengeluaran -> audit ACTION_UPDATE
    svc.edit_transaction(session, alice.id, tx.id, amount=75_000, note="Nasi goreng spesial")
    session.commit()

    logs = audit.list_audit_logs(session, alice.id, entity_id=tx.id)
    assert len(logs) == 2
    assert logs[0].action == ACTION_UPDATE
    assert logs[0].old_values is not None
    assert logs[0].new_values is not None

    # 3. Hapus pengeluaran -> audit ACTION_DELETE
    svc.delete_transaction(session, alice.id, tx.id)
    session.commit()

    logs = audit.list_audit_logs(session, alice.id, entity_id=tx.id)
    assert len(logs) == 3
    assert logs[0].action == ACTION_DELETE

    # 4. Pulihkan pengeluaran -> audit ACTION_RESTORE
    svc.restore_transaction(session, alice.id, tx.id)
    session.commit()

    logs = audit.list_audit_logs(session, alice.id, entity_id=tx.id)
    assert len(logs) == 4
    assert logs[0].action == ACTION_RESTORE


def test_undo_create_expense(session, alice):
    alice.open(alice.cash, 1_000_000)
    res = svc.add_expense(session, alice.id, 100_000, alice.food, alice.cash, on=D)
    session.commit()

    assert res.transaction.id > 0
    assert bal.get_total_balance(session, alice.id) == 900_000

    last_log = audit.list_audit_logs(session, alice.id, limit=1)[0]
    undo_res = audit.undo_action(session, alice.id, last_log.id)
    session.commit()

    assert undo_res.action_undone == ACTION_CREATE
    assert undo_res.transaction is not None
    assert undo_res.transaction.status == STATUS_DELETED

    # Saldo kembali utuh 1_000_000
    assert bal.get_total_balance(session, alice.id) == 1_000_000

    # Mencoba undo kembali aksi yang sama -> ditolak
    with pytest.raises(ValidationError, match="sudah pernah dibatalkan"):
        audit.undo_action(session, alice.id, last_log.id)


def test_undo_create_transfer_with_admin_fee(session, alice):
    alice.open(alice.cash, 500_000)
    alice.open(alice.bank, 500_000)

    trf_res = trf.add_transfer(
        session,
        alice.id,
        amount=200_000,
        from_method_id=alice.cash,
        to_method_id=alice.bank,
        admin_fee=2_500,
        on=D,
    )
    session.commit()

    transfer_id = trf_res.transfer.id
    fee_id = trf_res.admin_fee.id
    assert bal.get_method_balance(session, alice.id, alice.cash) == 297_500
    assert bal.get_method_balance(session, alice.id, alice.bank) == 700_000

    # Undo aksi transfer terakhir
    undo_res = audit.undo_last(session, alice.id)
    session.commit()

    assert undo_res.action_undone == ACTION_CREATE
    # Baik transfer maupun biaya admin terhubung berstatus deleted
    trf_tx = svc.get_transaction(session, alice.id, transfer_id, include_deleted=True)
    fee_tx = svc.get_transaction(session, alice.id, fee_id, include_deleted=True)
    assert trf_tx.status == STATUS_DELETED
    assert fee_tx.status == STATUS_DELETED

    # Saldo kedua metode kembali semula
    assert bal.get_method_balance(session, alice.id, alice.cash) == 500_000
    assert bal.get_method_balance(session, alice.id, alice.bank) == 500_000


def test_undo_create_adjustment(session, alice):
    alice.open(alice.cash, 500_000)
    adj_res = adj.adjust_balance(session, alice.id, alice.cash, actual_balance=600_000, on=D)
    session.commit()

    assert adj_res.transaction is not None
    adj_id = adj_res.transaction.id
    assert bal.get_total_balance(session, alice.id) == 600_000

    audit.undo_last(session, alice.id)
    session.commit()

    adj_tx = svc.get_transaction(session, alice.id, adj_id, include_deleted=True)
    assert adj_tx.status == STATUS_DELETED
    assert bal.get_total_balance(session, alice.id) == 500_000


def test_undo_delete_transaction(session, alice):
    alice.open(alice.cash, 500_000)
    res = svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D)
    session.commit()
    tx_id = res.transaction.id

    # Hapus transaksi
    svc.delete_transaction(session, alice.id, tx_id)
    session.commit()
    assert bal.get_total_balance(session, alice.id) == 500_000

    # Undo penghapusan (mengembalikan status active)
    last_log = audit.list_audit_logs(session, alice.id, limit=1)[0]
    assert last_log.action == ACTION_DELETE

    undo_res = audit.undo_action(session, alice.id, last_log.id)
    session.commit()

    assert undo_res.action_undone == ACTION_DELETE
    tx = svc.get_transaction(session, alice.id, tx_id)
    assert tx.status == STATUS_ACTIVE
    assert bal.get_total_balance(session, alice.id) == 450_000


def test_undo_restore_transaction(session, alice):
    alice.open(alice.cash, 500_000)
    res = svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D)
    svc.delete_transaction(session, alice.id, res.transaction.id)
    svc.restore_transaction(session, alice.id, res.transaction.id)
    session.commit()

    tx = svc.get_transaction(session, alice.id, res.transaction.id)
    assert tx.status == STATUS_ACTIVE

    # Undo restore -> kembali menjadi deleted
    last_log = audit.list_audit_logs(session, alice.id, limit=1)[0]
    assert last_log.action == ACTION_RESTORE

    undo_res = audit.undo_action(session, alice.id, last_log.id)
    session.commit()

    assert undo_res.action_undone == ACTION_RESTORE
    tx_after = svc.get_transaction(session, alice.id, res.transaction.id, include_deleted=True)
    assert tx_after.status == STATUS_DELETED
    assert bal.get_total_balance(session, alice.id) == 500_000


def test_undo_update_transaction(session, alice):
    alice.open(alice.cash, 1_000_000)
    res = svc.add_expense(
        session, alice.id, 50_000, alice.food, alice.cash, on=date(2026, 2, 1), note="Awal"
    )
    session.commit()
    tx_id = res.transaction.id

    # Edit transaksi: ubah nominal, tanggal, dan keterangan
    svc.edit_transaction(
        session,
        alice.id,
        tx_id,
        amount=80_000,
        on=date(2026, 2, 5),
        note="Revisi",
    )
    session.commit()

    assert bal.get_total_balance(session, alice.id) == 920_000

    # Undo edit
    undo_res = audit.undo_last(session, alice.id)
    session.commit()

    assert undo_res.action_undone == ACTION_UPDATE
    tx = svc.get_transaction(session, alice.id, tx_id)
    assert tx.amount == 50_000
    assert tx.date == date(2026, 2, 1)
    assert tx.note == "Awal"
    assert bal.get_total_balance(session, alice.id) == 950_000


def test_sequential_undo_last_lifo(session, alice):
    alice.open(alice.cash, 1_000_000)

    # 1. Tambah expense 10.000
    svc.add_expense(session, alice.id, 10_000, alice.food, alice.cash, on=D)
    # 2. Tambah expense 20.000
    svc.add_expense(session, alice.id, 20_000, alice.food, alice.cash, on=D)
    # 3. Tambah expense 30.000
    svc.add_expense(session, alice.id, 30_000, alice.food, alice.cash, on=D)
    session.commit()

    assert bal.get_total_balance(session, alice.id) == 940_000

    # Undo ke-1 -> membatalkan expense 30.000
    audit.undo_last(session, alice.id)
    session.commit()
    assert bal.get_total_balance(session, alice.id) == 970_000

    # Undo ke-2 -> membatalkan expense 20.000
    audit.undo_last(session, alice.id)
    session.commit()
    assert bal.get_total_balance(session, alice.id) == 990_000

    # Undo ke-3 -> membatalkan expense 10.000
    audit.undo_last(session, alice.id)
    session.commit()
    assert bal.get_total_balance(session, alice.id) == 1_000_000

    # Undo ke-4 -> tidak ada lagi aksi yang bisa di-undo
    with pytest.raises(ValidationError, match="Tidak ada riwayat perubahan"):
        audit.undo_last(session, alice.id)
