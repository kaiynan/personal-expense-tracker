"""Tes lapisan CLI: input interaktif, penanganan error, warning, dan alur kerja menu."""

from __future__ import annotations

import pytest

from cli import run_cli
from cli.actions import (
    action_add_expense,
    action_add_income,
    action_add_transfer,
    action_adjust_balance,
    action_dashboard,
    action_delete_transaction,
    action_edit_transaction,
    action_export_data,
    action_filter_transactions,
    action_list_transactions,
    action_manage_aliases,
    action_manage_audit,
    action_manage_backup,
    action_manage_categories,
    action_manage_recurring,
    action_manage_reminders,
    action_reports,
    action_search_transactions,
    action_set_budget,
    action_set_opening_balance,
)
from cli.inputs import (
    InputCancelled,
    prompt_amount,
    prompt_choice,
    prompt_confirm,
    prompt_date,
    prompt_int,
    prompt_str,
)
from database.engine import create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from models import STATUS_ACTIVE, STATUS_DELETED, TYPE_EXPENSE, TYPE_TRANSFER
from services import balances, categories, transactions


@pytest.fixture
def cli_ctx(engine):
    """Factory session dan user_id yang sudah di-seed data awal."""
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        user = ensure_default_user(session)
        seed_defaults(session, user.id)
        user_id = user.id
    return factory, user_id


def feed_inputs(monkeypatch, *inputs):
    """Mock input built-in dengan urutan masukan berantai."""
    it = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))


# --- 1. Tes Input Prompts ------------------------------------------------------
def test_prompt_int_retries_and_succeeds(monkeypatch):
    feed_inputs(monkeypatch, "bukan_angka", "-5", "42")
    res = prompt_int("Masukkan angka: ", min_val=0)
    assert res == 42


def test_prompt_int_cancel(monkeypatch):
    feed_inputs(monkeypatch, "0")
    with pytest.raises(InputCancelled):
        prompt_int("Masukkan angka: ", allow_cancel=True)


def test_prompt_amount_retries(monkeypatch):
    feed_inputs(monkeypatch, "abc", "-1000", "0", "150000")
    res = prompt_amount("Nominal: ", allow_zero=False, allow_cancel=False)
    assert res == 150_000


def test_prompt_amount_cancel(monkeypatch):
    feed_inputs(monkeypatch, "0")
    with pytest.raises(InputCancelled):
        prompt_amount("Nominal: ", allow_zero=False, allow_cancel=True)


def test_prompt_date_retries(monkeypatch):
    feed_inputs(monkeypatch, "31-02-2026", "salah-format", "15-10-2026")
    res = prompt_date("Tanggal: ")
    assert res.strftime("%d-%m-%Y") == "15-10-2026"


def test_prompt_date_default_on_empty(monkeypatch):
    feed_inputs(monkeypatch, "")
    res = prompt_date("Tanggal: ")
    assert res is not None


def test_prompt_choice_retries(monkeypatch):
    feed_inputs(monkeypatch, "99", "abc", "2")
    items = ["Apel", "Jeruk", "Mangga"]
    res = prompt_choice("Pilih buah: ", items)
    assert res == "Jeruk"


def test_prompt_confirm(monkeypatch):
    feed_inputs(monkeypatch, "apa?", "y")
    assert prompt_confirm("Yakin?") is True

    feed_inputs(monkeypatch, "n")
    assert prompt_confirm("Yakin?") is False

    feed_inputs(monkeypatch, "")
    assert prompt_confirm("Yakin?", default=True) is True


def test_prompt_str(monkeypatch):
    feed_inputs(monkeypatch, "", "catatan belanja")
    assert prompt_str("Keterangan: ", required=True) == "catatan belanja"


def test_input_cancelled_on_keyboard_interrupt(monkeypatch):
    def raise_interrupt(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)
    with pytest.raises(InputCancelled):
        prompt_str("Test: ")


# --- 2. Tes Aksi Tambah Pemasukan, Pengeluaran, Transfer ------------------------
def test_action_add_income(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Inputs:
    # 1. Nominal: "500000"
    # 2. Kategori: "1" (Gaji)
    # 3. Metode: "2" (Bank)
    # 4. Tanggal: "10-10-2026"
    # 5. Keterangan: "Gaji bulanan"
    feed_inputs(monkeypatch, "500000", "1", "2", "10-10-2026", "Gaji bulanan")
    action_add_income(factory, user_id)

    out = capsys.readouterr().out
    assert "Pemasukan berhasil dicatat!" in out
    assert "Rp500.000" in out

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id)
        assert len(txs) == 1
        assert txs[0].amount == 500_000
        assert txs[0].note == "Gaji bulanan"


def test_action_add_expense_with_negative_balance_warning(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Saldo Cash 0; pengeluaran Rp50.000 membuat saldo minus -> harus muncul warning non-blocking!
    # Inputs:
    # 1. Nominal: "50000"
    # 2. Kategori: "1" (Makanan)
    # 3. Metode: "1" (Cash)
    # 4. Tanggal: "10-10-2026"
    # 5. Keterangan: "Makan siang"
    feed_inputs(monkeypatch, "50000", "1", "1", "10-10-2026", "Makan siang")
    action_add_expense(factory, user_id)

    out = capsys.readouterr().out
    assert "Pengeluaran berhasil dicatat!" in out
    assert "[PERINGATAN]" in out
    assert "minus" in out

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id)
        assert len(txs) == 1
        assert txs[0].amount == 50_000


def test_action_add_transfer_with_admin_fee(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Inputs:
    # 1. Asal: Bank (nomor 2 di daftar metode aktif)
    # 2. Tujuan: Cash (nomor 1 di kandidat tujuan)
    # 3. Nominal: "200000"
    # 4. Biaya admin: "2500"
    # 5. Tanggal: "11-10-2026"
    # 6. Keterangan: "Tarik tunai"
    feed_inputs(monkeypatch, "2", "1", "200000", "2500", "11-10-2026", "Tarik tunai")
    action_add_transfer(factory, user_id)

    out = capsys.readouterr().out
    assert "Transfer berhasil dicatat!" in out
    assert "Rp200.000" in out
    assert "Rp2.500" in out

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id)
        assert len(txs) == 2
        transfer_tx = next(t for t in txs if t.type == TYPE_TRANSFER)
        fee_tx = next(t for t in txs if t.type == TYPE_EXPENSE)
        assert transfer_tx.amount == 200_000
        assert fee_tx.amount == 2_500
        assert fee_tx.related_transaction_id == transfer_tx.id


# --- 3. Tes Saldo Awal & Koreksi Saldo ------------------------------------------
def test_action_set_opening_balance(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Inputs:
    # 1. Metode: Cash (nomor 1)
    # 2. Saldo awal: "1000000"
    # 3. Tanggal berlaku: "01-10-2026"
    feed_inputs(monkeypatch, "1", "1000000", "01-10-2026")
    action_set_opening_balance(factory, user_id)

    out = capsys.readouterr().out
    assert "Saldo awal untuk Cash berhasil disimpan!" in out
    assert "Rp1.000.000" in out

    with session_scope(factory) as session:
        tot = balances.get_total_balance(session, user_id)
        assert tot == 1_000_000


def test_action_adjust_balance(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Atur saldo awal Cash Rp1.000.000
    feed_inputs(monkeypatch, "1", "1000000", "01-10-2026")
    action_set_opening_balance(factory, user_id)

    # Lakukan koreksi: saldo sebenarnya di dompet Rp950.000
    # Inputs:
    # 1. Metode: Cash (nomor 1)
    # 2. Tanggal: "11-10-2026"
    # 3. Saldo sebenarnya: "950000"
    # 4. Keterangan: "Selisih belanjaan kecil"
    feed_inputs(monkeypatch, "1", "11-10-2026", "950000", "Selisih belanjaan kecil")
    action_adjust_balance(factory, user_id)

    out = capsys.readouterr().out
    assert "Koreksi saldo berhasil dicatat!" in out
    assert "-Rp50.000" in out

    with session_scope(factory) as session:
        tot = balances.get_total_balance(session, user_id)
        assert tot == 950_000


# --- 4. Tes Riwayat, Filter, dan Cari ------------------------------------------
def test_action_list_and_search_transactions(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Tambah transaksi
    feed_inputs(monkeypatch, "100000", "1", "1", "10-10-2026", "Nasi padang spesial")
    action_add_expense(factory, user_id)

    # 1. Riwayat
    feed_inputs(monkeypatch, "10")
    action_list_transactions(factory, user_id)
    out1 = capsys.readouterr().out
    assert "Nasi padang spesial" in out1
    assert "-Rp100.000" in out1

    # 2. Pencarian kata kunci
    feed_inputs(monkeypatch, "padang")
    action_search_transactions(factory, user_id)
    out2 = capsys.readouterr().out
    assert "Nasi padang spesial" in out2

    # 3. Pencarian tanpa hasil
    feed_inputs(monkeypatch, "pizza_tidak_ada")
    action_search_transactions(factory, user_id)
    out3 = capsys.readouterr().out
    assert "Tidak ada transaksi untuk ditampilkan." in out3


def test_action_filter_transactions(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    feed_inputs(monkeypatch, "75000", "1", "1", "10-10-2026", "Makan malam")
    action_add_expense(factory, user_id)

    # Filter jenis: Pengeluaran (opsi 3: index 3 di type_options)
    # Filter kategori: Semua kategori (opsi 1)
    # Filter metode: Semua metode (opsi 1)
    # Tanggal awal: Enter
    # Tanggal akhir: Enter
    feed_inputs(monkeypatch, "3", "1", "1", "", "")
    action_filter_transactions(factory, user_id)
    out = capsys.readouterr().out
    assert "Makan malam" in out
    assert "-Rp75.000" in out


# --- 5. Tes Edit & Hapus (Soft Delete) Transaksi --------------------------------
def test_action_edit_transaction(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    feed_inputs(monkeypatch, "50000", "1", "1", "10-10-2026", "Makan awal")
    action_add_expense(factory, user_id)

    with session_scope(factory) as session:
        tx = transactions.list_transactions(session, user_id)[0]
        tx_id = tx.id

    # Edit:
    # 1. ID: tx_id
    # 2. Nominal baru: "70000"
    # 3. Tanggal baru: "" (tetap)
    # 4. Ubah kategori: "n" (tidak)
    # 5. Ubah metode: "n" (tidak)
    # 6. Keterangan baru: "Makan revisi"
    # 7. Simpan: "y"
    feed_inputs(monkeypatch, str(tx_id), "70000", "", "n", "n", "Makan revisi", "y")
    action_edit_transaction(factory, user_id)

    out = capsys.readouterr().out
    assert f"Transaksi #{tx_id} berhasil diperbarui." in out

    with session_scope(factory) as session:
        updated = transactions.get_transaction(session, user_id, tx_id)
        assert updated.amount == 70_000
        assert updated.note == "Makan revisi"


def test_action_delete_transaction_cancelled(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    feed_inputs(monkeypatch, "50000", "1", "1", "10-10-2026", "Makan")
    action_add_expense(factory, user_id)

    with session_scope(factory) as session:
        tx_id = transactions.list_transactions(session, user_id)[0].id

    # Batalkan penghapusan ('n')
    feed_inputs(monkeypatch, str(tx_id), "n")
    action_delete_transaction(factory, user_id)

    out = capsys.readouterr().out
    assert "Penghapusan dibatalkan." in out

    with session_scope(factory) as session:
        tx = transactions.get_transaction(session, user_id, tx_id)
        assert tx.status == STATUS_ACTIVE


def test_action_delete_transaction_confirmed_cascades_fee(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Buat transfer dengan biaya admin
    feed_inputs(monkeypatch, "2", "1", "100000", "1000", "11-10-2026", "Transfer tes")
    action_add_transfer(factory, user_id)

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id)
        transfer_id = next(t.id for t in txs if t.type == TYPE_TRANSFER)
        fee_id = next(t.id for t in txs if t.type == TYPE_EXPENSE)

    # Konfirmasi hapus transfer ('y')
    feed_inputs(monkeypatch, str(transfer_id), "y")
    action_delete_transaction(factory, user_id)

    out = capsys.readouterr().out
    assert f"Transaksi #{transfer_id} berhasil dihapus." in out

    with session_scope(factory) as session:
        # Keduanya harus soft-deleted
        t = transactions.get_transaction(session, user_id, transfer_id, include_deleted=True)
        f = transactions.get_transaction(session, user_id, fee_id, include_deleted=True)
        assert t.status == STATUS_DELETED
        assert f.status == STATUS_DELETED


# --- 6. Tes Dashboard & Budget -------------------------------------------------
def test_action_dashboard_and_budget(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Atur saldo awal
    feed_inputs(monkeypatch, "1", "2000000", "01-10-2026")
    action_set_opening_balance(factory, user_id)

    # Atur budget bulanan Rp1.000.000 untuk 2026-10 (menu 1: Atur Budget Total Bulanan)
    feed_inputs(monkeypatch, "1", "1000000", "2026-10")
    action_set_budget(factory, user_id)

    # Tambah pengeluaran Rp850.000 (85% -> mendekati batas)
    feed_inputs(monkeypatch, "850000", "1", "1", "10-10-2026", "Belanja bulanan")
    action_add_expense(factory, user_id)

    # Buka dashboard
    action_dashboard(factory, user_id)
    out = capsys.readouterr().out
    assert "DASHBOARD KEUANGAN" in out
    assert "Rp1.150.000" in out  # sisa saldo total: 2.000.000 - 850.000
    assert "Rp850.000 (85.0%)" in out
    assert "MENDEKATI BATAS ANGGARAN" in out


# --- 7. Tes Menu Utama Interaktif (run_cli) -----------------------------------
def test_run_cli_exit(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Langsung pilih "0" untuk keluar
    feed_inputs(monkeypatch, "0")
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "PERSONAL EXPENSE TRACKER" in out
    assert "Sampai jumpa!" in out


def test_run_cli_invalid_then_exit(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx
    # Pilih "99" (tidak ada), lalu "0" (keluar)
    feed_inputs(monkeypatch, "99", "0")
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "tidak tersedia" in out
    assert "Sampai jumpa!" in out


# --- 8. Tes Kelola Kategori (action_manage_categories) -------------------------
def test_action_manage_categories(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx

    # 1. Tambah Kategori Baru: "Langganan" (Expense)
    # Menu aksi: "2" (Tambah Kategori Baru), Jenis: "1" (Expense), Nama: "Langganan"
    feed_inputs(monkeypatch, "2", "1", "Langganan")
    action_manage_categories(factory, user_id)
    out = capsys.readouterr().out
    assert "Kategori 'Langganan' (expense) berhasil ditambahkan" in out

    # 2. Lihat Daftar Kategori
    feed_inputs(monkeypatch, "1")
    action_manage_categories(factory, user_id)
    out = capsys.readouterr().out
    assert "Langganan" in out

    # 3. Ubah Nama Kategori: dari "Langganan" menjadi "Subscription"
    with session_scope(factory) as session:
        exp_cats = categories.list_categories(
            session, user_id, type_="expense", include_archived=True, include_system=False
        )
        langganan_idx = next(i for i, c in enumerate(exp_cats, start=1) if c.name == "Langganan")

    feed_inputs(monkeypatch, "3", "1", str(langganan_idx), "Subscription")
    action_manage_categories(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil diubah namanya menjadi 'Subscription'" in out

    # 4. Arsipkan Kategori
    with session_scope(factory) as session:
        exp_cats = categories.list_categories(
            session, user_id, type_="expense", include_archived=False, include_system=False
        )
        sub_idx = next(i for i, c in enumerate(exp_cats, start=1) if c.name == "Subscription")

    feed_inputs(monkeypatch, "4", "1", str(sub_idx), "y")
    action_manage_categories(factory, user_id)
    out = capsys.readouterr().out
    assert "Kategori 'Subscription' berhasil diarsipkan" in out

    # 5. Pulihkan Kategori dari Arsip
    with session_scope(factory) as session:
        archived_cats = [
            c
            for c in categories.list_categories(
                session, user_id, type_="expense", include_archived=True, include_system=False
            )
            if c.is_archived
        ]
        sub_arch_idx = next(
            i for i, c in enumerate(archived_cats, start=1) if c.name == "Subscription"
        )

    feed_inputs(monkeypatch, "5", "1", str(sub_arch_idx))
    action_manage_categories(factory, user_id)
    out = capsys.readouterr().out
    assert "Kategori 'Subscription' berhasil dipulihkan dari arsip" in out


def test_action_manage_recurring(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx

    # 1. Tambah Transaksi Rutin Baru
    feed_inputs(
        monkeypatch,
        "2",  # Tambah Transaksi Rutin Baru
        "1",  # Pengeluaran
        "Internet WiFi",
        "350000",
        "1",  # Kategori
        "1",  # Metode
        "1",  # Bulanan
        "20",  # Tgl 20
        "",  # Tanggal mulai hari ini
        "n",  # Tidak ada batas akhir
        "Indihome",
    )
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil dibuat" in out
    assert "Internet WiFi" in out

    # 2. Lihat Daftar Transaksi Rutin
    feed_inputs(monkeypatch, "1")
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "Internet WiFi" in out
    assert "Rp350.000" in out
    assert "Aktif" in out

    # 3. Ubah Transaksi Rutin
    feed_inputs(
        monkeypatch,
        "3",  # Ubah
        "1",  # Pilih item 1
        "",  # Nama tetap
        "385000",  # Nominal baru
        "n",  # Tgl tetap
        "",  # Catatan tetap
    )
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil diperbarui" in out

    # 4. Catat Transaksi Rutin Sekarang (Apply)
    feed_inputs(
        monkeypatch,
        "5",  # Catat Transaksi Rutin Sekarang
        "1",  # Pilih item 1
        "",  # Tanggal hari ini
        "y",  # Konfirmasi
    )
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil dicatat dari transaksi rutin" in out

    # 5. Nonaktifkan Transaksi Rutin
    feed_inputs(
        monkeypatch,
        "4",  # Aktifkan / Nonaktifkan
        "1",  # Pilih item 1
    )
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "dinonaktifkan" in out

    # 6. Hapus Transaksi Rutin
    feed_inputs(
        monkeypatch,
        "7",  # Hapus
        "1",  # Pilih item 1
        "y",  # Konfirmasi
    )
    action_manage_recurring(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil dihapus" in out


def test_action_manage_backup_and_run_cli_menu(cli_ctx, monkeypatch, capsys, tmp_path):
    factory, user_id = cli_ctx
    backup_file = tmp_path / "cli_test_backup.json"

    # 1. Ekspor cadangan via CLI
    feed_inputs(
        monkeypatch,
        "1",  # Buat File Cadangan
        str(backup_file),  # Path file
    )
    action_manage_backup(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil dicadangkan" in out
    assert backup_file.is_file()

    # 2. Lihat daftar file cadangan (monkeypatch config.BACKUPS_DIR ke tmp_path)
    monkeypatch.setattr("config.BACKUPS_DIR", tmp_path)
    feed_inputs(monkeypatch, "3")  # Lihat Daftar File Cadangan
    action_manage_backup(factory, user_id)
    out = capsys.readouterr().out
    assert "Ditemukan" in out
    assert "cli_test_backup.json" in out

    # 3. Pulihkan data via CLI (pilih dari folder backups/)
    feed_inputs(
        monkeypatch,
        "2",  # Pulihkan Data
        "1",  # Pilih dari folder cadangan
        "1",  # Pilih file ke-1
        "y",  # Konfirmasi
    )
    action_manage_backup(factory, user_id)
    out = capsys.readouterr().out
    assert "INFORMASI FILE CADANGAN" in out
    assert "berhasil dipulihkan" in out

    # 4. Tes menu utama run_cli membuka menu 18 lalu keluar
    feed_inputs(
        monkeypatch,
        "18",  # Pilih menu 18: Backup dan Restore Data
        "3",  # Lihat Daftar File Cadangan
        "0",  # Keluar
    )
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "Backup dan Restore Data" in out
    assert "Terima kasih telah menggunakan Personal Expense Tracker" in out


def test_action_manage_audit(cli_ctx, monkeypatch, capsys):
    factory, user_id = cli_ctx

    # 1. Tambah pengeluaran
    with session_scope(factory) as session:
        cats = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)
        meths = balances.get_method_balances(session, user_id)
        transactions.add_expense(
            session, user_id, 50_000, cats[0].id, meths[0].method_id, on="01-02-2026"
        )

    # 2. Lihat riwayat perubahan (menu 2)
    feed_inputs(
        monkeypatch,
        "2",  # Lihat Riwayat Perubahan
        "0",  # Kembali
    )
    action_manage_audit(factory, user_id)
    out = capsys.readouterr().out
    assert "WAKTU" in out
    assert "CREATE" in out

    # 3. Batalkan aksi terakhir (menu 1)
    feed_inputs(
        monkeypatch,
        "1",  # Batalkan Aksi Terakhir (Undo)
        "0",  # Kembali
    )
    action_manage_audit(factory, user_id)
    out = capsys.readouterr().out
    assert "Berhasil membatalkan aksi" in out

    # 4. Cek menu utama run_cli menu 19
    feed_inputs(
        monkeypatch,
        "19",  # Riwayat Perubahan & Undo
        "0",  # Kembali
        "0",  # Keluar
    )
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "Riwayat Perubahan & Undo" in out


def test_cli_action_reports_all_modes(cli_ctx, monkeypatch, capsys):
    """Aksi laporan CLI mendukung laporan harian, perbandingan periode, dan statistik."""
    factory, user_id = cli_ctx

    # 1. Laporan Harian
    feed_inputs(monkeypatch, "1", "01-02-2026")
    action_reports(factory, user_id)
    out = capsys.readouterr().out
    assert "LAPORAN HARIAN" in out
    assert "Tanggal: 01-02-2026" in out

    # 2. Perbandingan Periode
    feed_inputs(monkeypatch, "4", "1")
    action_reports(factory, user_id)
    out = capsys.readouterr().out
    assert "PERBANDINGAN PERIODE KEUANGAN" in out

    # 3. Statistik Keuangan
    feed_inputs(monkeypatch, "5", "1", "")
    action_reports(factory, user_id)
    out = capsys.readouterr().out
    assert "STATISTIK KEUANGAN" in out
    assert "Rata-rata Pengeluaran/Hari" in out


def test_cli_action_manage_aliases(cli_ctx, monkeypatch, capsys):
    """Aksi manajemen alias di CLI dapat melihat, menambah, dan menghapus alias."""
    factory, user_id = cli_ctx

    # 1. Lihat alias bawaan (seed)
    feed_inputs(monkeypatch, "1", "0")
    action_manage_aliases(factory, user_id)
    out = capsys.readouterr().out
    assert "KELOLA ALIAS INPUT CEPAT" in out
    assert "Ditemukan" in out
    assert "[ Alias Kategori ]" in out
    assert "[ Alias Metode Pembayaran ]" in out

    # 2. Tambah alias kategori
    feed_inputs(monkeypatch, "2", "1", "soto", "0")
    action_manage_aliases(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil ditambahkan untuk kategori" in out

    # 3. Tambah alias metode pembayaran
    feed_inputs(monkeypatch, "3", "1", "dompet", "0")
    action_manage_aliases(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil ditambahkan untuk metode" in out

    # 4. Hapus alias
    feed_inputs(monkeypatch, "4", "1", "y", "0")
    action_manage_aliases(factory, user_id)
    out = capsys.readouterr().out
    assert "berhasil dihapus" in out

    # 5. Akses lewat run_cli menu 20
    feed_inputs(monkeypatch, "20", "0", "0")
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "Kelola Alias Input Cepat" in out


def test_cli_action_manage_reminders(cli_ctx, monkeypatch, capsys):
    """Aksi manajemen pengingat dan ringkasan di CLI."""
    factory, user_id = cli_ctx

    # 1. Lihat pengaturan saat ini (menu 1)
    feed_inputs(monkeypatch, "1", "0")
    action_manage_reminders(factory, user_id)
    out = capsys.readouterr().out
    assert "Pengaturan Pengingat & Ringkasan" in out
    assert "Pengingat Harian" in out
    assert "20:00" in out

    # 2. Preview pengingat harian (menu 2)
    feed_inputs(monkeypatch, "2", "0")
    action_manage_reminders(factory, user_id)
    out = capsys.readouterr().out
    assert "PENGINGAT KEUANGAN HARIAN" in out

    # 3. Preview ringkasan mingguan (menu 3)
    feed_inputs(monkeypatch, "3", "0")
    action_manage_reminders(factory, user_id)
    out = capsys.readouterr().out
    assert "RINGKASAN KEUANGAN MINGGUAN" in out

    # 4. Preview ringkasan bulanan (menu 4)
    feed_inputs(monkeypatch, "4", "0")
    action_manage_reminders(factory, user_id)
    out = capsys.readouterr().out
    assert "RINGKASAN & INSIGHT KEUANGAN BULANAN" in out

    # 5. Ubah pengaturan pengingat (menu 5)
    feed_inputs(
        monkeypatch,
        "5",
        "n",  # Daily nonaktif
        "21:15",  # Jam baru
        "y",  # Weekly aktif
        "y",  # Monthly aktif
        "0",  # Kembali
    )
    action_manage_reminders(factory, user_id)
    out = capsys.readouterr().out
    assert "Pengaturan pengingat berhasil diperbarui" in out
    assert "21:15" in out

    # 6. Akses lewat run_cli menu 21
    feed_inputs(monkeypatch, "21", "0", "0")
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "Pengingat Harian & Ringkasan Otomatis" in out


def test_cli_action_export_data(cli_ctx, monkeypatch, capsys, tmp_path):
    """Aksi ekspor di CLI mendukung ekspor CSV, Excel, dan PDF."""
    import config

    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports_output")
    factory, user_id = cli_ctx

    # Ekspor Semua Format Sekaligus (pilihan 4) untuk Semua Periode (pilihan 1)
    feed_inputs(monkeypatch, "4", "1")
    action_export_data(factory, user_id)
    out = capsys.readouterr().out
    assert "Ekspor data keuangan berhasil disimpan" in out
    assert "CSV" in out
    assert "Excel" in out
    assert "PDF" in out

    # Akses lewat run_cli menu 22 -> batalkan dengan '0'
    feed_inputs(monkeypatch, "22", "0", "0")
    run_cli(factory, user_id)
    out = capsys.readouterr().out
    assert "EXPORT DATA & LAPORAN (CSV, EXCEL, PDF)" in out


def test_cli_weekly_budget_and_rollover(cli_ctx, monkeypatch, capsys):
    """Pengujian CLI untuk budget mingguan (total & kategori) dan rollover budget bulanan."""
    factory, user_id = cli_ctx

    # 1. Atur Budget Total Mingguan (opsi 4) untuk minggu yang memuat 07-10-2026
    feed_inputs(monkeypatch, "4", "750000", "07-10-2026")
    action_set_budget(factory, user_id)
    out = capsys.readouterr().out
    assert "Budget total mingguan (05-10-2026 s.d. 11-10-2026) berhasil diatur" in out
    assert "Rp750.000" in out

    # 2. Atur Budget Kategori Mingguan (opsi 5) untuk kategori pertama (Makanan)
    feed_inputs(monkeypatch, "5", "1", "300000", "07-10-2026")
    action_set_budget(factory, user_id)
    out = capsys.readouterr().out
    assert "Budget mingguan kategori 'Makanan' (05-10-2026 s.d. 11-10-2026)" in out
    assert "Rp300.000" in out

    # 3. Lihat Evaluasi Budget Mingguan (opsi 6)
    feed_inputs(monkeypatch, "6", "07-10-2026")
    action_set_budget(factory, user_id)
    out = capsys.readouterr().out
    assert "EVALUASI BUDGET MINGGUAN (05-10-2026 s.d. 11-10-2026)" in out
    assert "Rp750.000" in out
    assert "Makanan" in out

    # 4. Atur Budget Total Bulanan pada 2026-09 (opsi 1), lalu rollover ke 2026-10 (opsi 7)
    feed_inputs(monkeypatch, "1", "2000000", "2026-09")
    action_set_budget(factory, user_id)
    capsys.readouterr()

    feed_inputs(monkeypatch, "7", "2026-09", "2026-10")
    action_set_budget(factory, user_id)
    out = capsys.readouterr().out
    assert "Rollover berhasil dijalankan dari 2026-09 ke 2026-10" in out
    assert "Sisa dibawa Rp2.000.000" in out
    assert "Total Budget Baru: Rp4.000.000" in out
