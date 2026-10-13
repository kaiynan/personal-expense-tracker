"""Tes unit dan integrasi untuk services.backup (Backup dan Restore).

Mencakup:
- Ekspor cadangan ke file JSON (default path & custom path).
- Validasi integritas (checksum mismatch, struktur tidak lengkap, versi tidak didukung).
- Pemulihan data lengkap (kategori sistem & kustom, metode, saldo awal, anggaran,
  transaksi rutin, transaksi tunggal, transfer berpasangan dengan ID remapping).
- Konsistensi saldo setelah pemulihan.
- Penanganan kesalahan (file hilang, JSON rusak, cadangan milik pengguna lain).
"""

import json

import pytest

from models import (
    TARGET_CATEGORY,
    TYPE_EXPENSE,
    TYPE_TRANSFER,
)
from services import adjustments as adj
from services import aliases as ali
from services import balances as bal
from services import budgets as bud
from services import categories as cat
from services import recurring as rec
from services import reminders as rem
from services import transactions as svc
from services import transfers as trf
from services.backup import (
    create_backup_data,
    export_backup,
    get_backup_summary,
    list_backups,
    restore_backup,
    validate_backup_data,
)
from utils.errors import NotFoundError, ValidationError


def test_export_backup_custom_path_and_format(session, alice, tmp_path):
    alice.open(alice.cash, 1_000_000)
    svc.add_expense(
        session,
        alice.id,
        150_000,
        alice.food,
        alice.cash,
        on="01-02-2026",
        note="Makan siang",
    )
    session.commit()

    target_file = tmp_path / "custom_backup.json"
    exported_path = export_backup(session, alice.id, file_path=target_file)

    assert exported_path == target_file
    assert target_file.is_file()

    with target_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["version"] == "1.0"
    assert data["user_id"] == alice.id
    assert "checksum" in data
    assert data["summary"]["transactions"] == 1
    assert len(data["data"]["transactions"]) == 1
    assert data["data"]["transactions"][0]["note"] == "Makan siang"


def test_export_backup_default_path(session, alice, monkeypatch, tmp_path):
    monkeypatch.setattr("config.BACKUPS_DIR", tmp_path)
    alice.open(alice.cash, 500_000)
    session.commit()

    exported_path = export_backup(session, alice.id)
    assert exported_path.parent == tmp_path
    assert exported_path.is_file()
    assert f"backup_user_{alice.id}_" in exported_path.name


def test_validate_backup_checksum_tamper(session, alice):
    alice.open(alice.cash, 500_000)
    session.commit()

    backup_dict = create_backup_data(session, alice.id)
    # Validasi awal berhasil
    assert validate_backup_data(session, alice.id, backup_dict)["version"] == "1.0"

    # Manipulasi data (tampering) tanpa memperbarui checksum
    backup_dict["data"]["opening_balances"][0]["amount"] = 999_999_999
    with pytest.raises(ValidationError, match="checksum mismatch"):
        validate_backup_data(session, alice.id, backup_dict)


def test_validate_backup_invalid_structure(session, alice):
    with pytest.raises(ValidationError, match="Format cadangan tidak valid"):
        validate_backup_data(session, alice.id, "bukan-dict")  # type: ignore

    with pytest.raises(ValidationError, match="Versi cadangan tidak didukung"):
        validate_backup_data(session, alice.id, {"version": "2.0", "data": {}})

    with pytest.raises(ValidationError, match="Bagian 'data' tidak ditemukan"):
        validate_backup_data(session, alice.id, {"version": "1.0"})

    with pytest.raises(ValidationError, match="kunci 'categories' hilang"):
        validate_backup_data(session, alice.id, {"version": "1.0", "data": {"payment_methods": []}})


def test_full_roundtrip_restore_preserves_everything_and_balance(session, alice, tmp_path):
    # 1. Setup data lengkap:
    # Saldo awal
    alice.open(alice.cash, 1_000_000)
    alice.open(alice.bank, 2_000_000)

    # Kategori kustom
    custom_cat = cat.add_category(session, alice.id, "Kopi & Kafe", TYPE_EXPENSE)

    # Transaksi rutin
    rec.create_recurring(
        session,
        alice.id,
        name="Langganan Internet",
        type_=TYPE_EXPENSE,
        amount=350_000,
        category_id=custom_cat.id,
        method_id=alice.bank,
        frequency="monthly",
        day_of_month=5,
        start_date="01-01-2026",
    )

    # Anggaran bulanan dan kategori
    bud.set_monthly_budget(session, alice.id, 3_000_000, month="2026-02")
    bud.set_category_budget(session, alice.id, custom_cat.id, 500_000, month="2026-02")

    # Transaksi pengeluaran & pemasukan
    svc.add_expense(
        session, alice.id, 40_000, custom_cat.id, alice.cash, on="02-02-2026", note="Espresso"
    )
    svc.add_income(
        session,
        alice.id,
        5_000_000,
        alice.salary,
        alice.bank,
        on="01-02-2026",
        note="Gaji Bulanan",
    )

    # Transfer antar metode dengan biaya admin (untuk menguji remapping related_transaction_id)
    trf.add_transfer(
        session,
        alice.id,
        500_000,
        alice.bank,
        alice.cash,
        on="03-02-2026",
        note="Tarik ATM",
        admin_fee=2_500,
    )

    # Koreksi saldo
    adj.adjust_balance(
        session,
        alice.id,
        alice.cash,
        1_450_000,
        on="04-02-2026",
        note="Koreksi hitung fisik",
    )

    # Alias input cepat kustom
    ali.add_alias(session, alice.id, "ngopi", TARGET_CATEGORY, custom_cat.id)

    # Pengaturan pengingat kustom
    rem.update_reminder_settings(session, alice.id, daily_time="22:15", is_daily_active=False)
    session.commit()

    # Periksa saldo awal sebelum backup
    balance_cash_before = bal.get_method_balance(session, alice.id, alice.cash)
    balance_bank_before = bal.get_method_balance(session, alice.id, alice.bank)
    total_before = bal.get_total_balance(session, alice.id)

    # 2. Ekspor ke file
    backup_file = tmp_path / "alice_full_backup.json"
    export_backup(session, alice.id, file_path=backup_file)

    # 3. Ubah / tambah data lain agar kondisi database berbeda
    svc.add_expense(
        session,
        alice.id,
        200_000,
        alice.food,
        alice.cash,
        on="05-02-2026",
        note="Makan Malam Extra",
    )
    session.commit()
    assert bal.get_total_balance(session, alice.id) != total_before

    # 4. Pulihkan dari backup file
    res = restore_backup(session, alice.id, backup_file)
    session.commit()

    # Pastikan jumlah entitas yang dipulihkan sesuai
    assert res["payment_methods"] >= 2
    assert res["categories"] >= 5
    assert res["opening_balances"] == 2
    assert res["budgets"] == 2
    assert res["recurring"] == 1
    assert res["transactions"] == 5  # expense, income, transfer, fee_tx, adjustment
    assert res["quick_aliases"] >= 1
    assert res["reminder_settings"] == 1

    # Pastikan saldo kembali persis seperti semula
    new_methods = {m.name: m.method_id for m in bal.get_method_balances(session, alice.id)}
    assert bal.get_method_balance(session, alice.id, new_methods["Cash"]) == balance_cash_before
    assert bal.get_method_balance(session, alice.id, new_methods["Bank"]) == balance_bank_before
    assert bal.get_total_balance(session, alice.id) == total_before

    # Cek konsistensi saldo
    consistency = bal.check_balance_consistency(session, alice.id)
    assert all(c.is_consistent for c in consistency)

    # Pastikan relasi related_transaction_id tersambung kembali dengan benar
    all_tx = svc.list_transactions(session, alice.id)
    fee_tx = next(t for t in all_tx if t.related_transaction_id is not None)
    transfer_tx = next(t for t in all_tx if t.id == fee_tx.related_transaction_id)
    assert transfer_tx.type == TYPE_TRANSFER
    assert fee_tx.type == TYPE_EXPENSE
    assert fee_tx.amount == 2_500

    # Pastikan transaksi rutin dan kategori kustom tetap ada
    cats = {c.name: c for c in cat.list_categories(session, alice.id, include_system=True)}
    assert "Kopi & Kafe" in cats
    cat_item = cats["Kopi & Kafe"]
    rec_items = rec.list_recurring(session, alice.id)
    assert len(rec_items) == 1
    assert rec_items[0].name == "Langganan Internet"
    assert rec_items[0].category_id == cat_item.id

    # Pastikan alias input cepat dan reminder settings dipulihkan
    user_aliases = ali.list_aliases(session, alice.id)
    ngopi_alias = next((a for a in user_aliases if a.alias == "ngopi"), None)
    assert ngopi_alias is not None
    assert ngopi_alias.category_id == cat_item.id

    rs_after = rem.get_reminder_settings(session, alice.id)
    assert rs_after.daily_time == "22:15"
    assert rs_after.is_daily_active is False


def test_restore_backup_nonexistent_file(session, alice, tmp_path):
    with pytest.raises(NotFoundError, match="File cadangan tidak ditemukan"):
        restore_backup(session, alice.id, tmp_path / "tidak_ada.json")


def test_restore_backup_corrupted_json(session, alice, tmp_path):
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{ini bukan json valid}", encoding="utf-8")
    with pytest.raises(ValidationError, match="Gagal membaca file cadangan"):
        restore_backup(session, alice.id, corrupt_file)


def test_get_backup_summary_and_list_backups(session, alice, tmp_path):
    alice.open(alice.cash, 100_000)
    session.commit()

    file_1 = tmp_path / "backup_1.json"
    file_2 = tmp_path / "backup_2.json"
    export_backup(session, alice.id, file_path=file_1)
    export_backup(session, alice.id, file_path=file_2)

    # list_backups
    backups = list_backups(session, alice.id, directory=tmp_path)
    assert len(backups) == 2
    filenames = {b["filename"] for b in backups}
    assert filenames == {"backup_1.json", "backup_2.json"}

    # get_backup_summary
    summary = get_backup_summary(session, alice.id, file_1)
    assert summary["filename"] == "backup_1.json"
    assert summary["version"] == "1.0"
    assert summary["user_id"] == alice.id
    assert "summary" in summary

    # file tidak ada
    with pytest.raises(NotFoundError):
        get_backup_summary(session, alice.id, tmp_path / "ghost.json")
