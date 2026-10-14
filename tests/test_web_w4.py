"""Pengujian unit dan integrasi Web Dashboard Fase W4:
- Halaman Budget (/budgets): set budget bulanan, kategori, mingguan, rollover, dan indikator visual.
- Halaman Kategori & Metode (/categories): kelola kategori, proteksi kategori sistem "Biaya Admin",
  kelola metode pembayaran, dan quick alias.
- Halaman Transaksi Rutin (/recurring): tambah jadwal rutin, toggle aktif/nonaktif, hapus jadwal.
- Halaman Laporan (/reports & /analytics): laporan harian, mingguan, bulanan, perbandingan periode.
- Ekspor Laporan: CSV (netralisasi formula injection), Excel (.xlsx), dan PDF.
- Impor Mutasi CSV: pratinjau deteksi duplikat dan konfirmasi commit transaksi.
- Pengaturan Pengguna (/settings & /more): zona waktu, pengingat, audit konsistensi saldo,
  backup ZIP per pengguna & pemulihan data (restore), serta penghapusan akun berkonfirmasi.
- Pengujian Isolasi Data (User Isolation) antar pengguna.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from database.engine import create_db_engine, create_session_factory, session_scope
from database.queries import get_method_by_name, get_user, list_categories, list_methods
from database.seed import seed_defaults
from models import (
    TYPE_EXPENSE,
    TYPE_INCOME,
    Base,
    Category,
    RecurringTransaction,
    User,
)
from services import aliases as aliases_service
from services import categories as categories_service
from services import opening_balances as opening_balances_service
from services import transactions as tx_service
from utils.dates import today
from web.app import create_web_app


@pytest.fixture
def web_db(tmp_path):
    """Database SQLite berbasis file sementara untuk pengujian web W4."""
    db_file = tmp_path / "web_w4_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory, engine
    engine.dispose()


@pytest.fixture
def web_test_env(web_db):
    """Aplikasi web dengan mock sender bot Telegram dan TestClient."""
    factory, _engine = web_db
    sent_otps: list[dict[str, Any]] = []

    def mock_bot_sender(telegram_id: int, code: str):
        sent_otps.append({"telegram_id": telegram_id, "code": code})

    app = create_web_app(factory=factory, bot_sender=mock_bot_sender, enable_bot=False)
    app.state.sent_otps = sent_otps

    with TestClient(app) as client:
        yield client, factory, sent_otps


def _create_user(factory, name: str, telegram_id: int) -> User:
    """Helper membuat user baru dan seeding kategori/metode default."""
    with session_scope(factory) as session:
        user = User(name=name, telegram_id=telegram_id, status="active", role="user")
        session.add(user)
        session.flush()
        seed_defaults(session, user.id)
        user_id = user.id

    with session_scope(factory) as session:
        return session.get(User, user_id)


def _login(client: TestClient, sent_otps: list, user: User):
    """Helper login via alur OTP Telegram."""
    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent_otps[-1]["code"]
    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res.status_code in (200, 303)


# ==============================================================================
# 1. Budget Page & Endpoints
# ==============================================================================


def test_budgets_page_and_crud(web_test_env):
    """Pengujian halaman /budgets, pengaturan budget bulanan, kategori, mingguan, dan rollover."""
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Budi", 1001)

    # 1.1 Unauthenticated -> redirect
    res = client.get("/budgets", follow_redirects=False)
    assert res.status_code == 303
    assert "/login" in res.headers["location"]

    _login(client, sent_otps, user)

    # 1.2 Authenticated GET
    res = client.get("/budgets")
    assert res.status_code == 200
    assert "Budget" in res.text
    assert "Atur Budget Bulanan" in res.text

    # 1.3 Set Budget Bulanan
    res = client.post(
        "/budgets/monthly",
        data={"amount": "Rp 5.000.000", "month": "2026-02"},
    )
    assert res.status_code == 200
    assert "budgetUpdated" in res.headers.get("HX-Trigger", "")
    assert "berhasil" in res.text.lower()

    # 1.4 Set Budget Kategori
    with session_scope(factory) as session:
        cats = list_categories(session, user.id, type_=TYPE_EXPENSE)
        food_cat = next(c for c in cats if not c.is_system)

    res = client.post(
        "/budgets/category",
        data={
            "category_id": food_cat.id,
            "amount": "Rp 1.500.000",
            "month": "2026-02",
        },
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    # 1.5 Set Budget Mingguan
    res = client.post(
        "/budgets/weekly",
        data={"amount": "Rp 1.000.000", "week_str": "2026-W07"},
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    # 1.6 Toggle Rollover
    res = client.post(
        "/budgets/rollover",
        data={"enabled": "true", "month": "2026-02"},
    )
    assert res.status_code == 200
    assert "rollover" in res.text.lower()

    # 1.7 Cek render ulang halaman /budgets dengan transaksi pengeluaran (indikator visual)
    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = methods[0]
        # Belanja 4.5 juta dari budget 5 juta (90% -> indikator kuning/oranye)
        opening_balances_service.set_opening_balance(
            session, user.id, cash.id, 10_000_000, effective_date="01-02-2026"
        )
        tx_service.add_expense(
            session,
            user.id,
            4_500_000,
            food_cat.id,
            cash.id,
            on="10-02-2026",
            note="Belanja bulanan",
        )

    res = client.get("/budgets?month=2026-02")
    assert res.status_code == 200
    assert "90%" in res.text or "90.0%" in res.text or "4.500.000" in res.text


# ==============================================================================
# 2. Categories & Payment Methods Management
# ==============================================================================


def test_categories_and_payment_methods(web_test_env):
    """Pengujian kelola kategori kustom, proteksi kategori sistem, metode pembayaran, dan alias."""
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Citra", 1002)
    _login(client, sent_otps, user)

    # 2.1 Halaman Kategori
    res = client.get("/categories")
    assert res.status_code == 200
    assert "Kategori Transaksi" in res.text
    assert "Metode Pembayaran" in res.text

    # 2.2 Tambah Kategori Kustom (Pengeluaran & Pemasukan)
    res = client.post(
        "/categories/add",
        data={"name": "Hobi Koleksi", "type": TYPE_EXPENSE},
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    res = client.post(
        "/categories/add",
        data={"name": "Dividen Saham", "type": TYPE_INCOME},
    )
    assert res.status_code == 200

    # Verifikasi di database
    with session_scope(factory) as session:
        cats = list_categories(session, user.id)
        hobi = next(c for c in cats if c.name == "Hobi Koleksi")
        assert hobi is not None
        hobi_id = hobi.id

    # 2.3 Ubah Nama Kategori
    res = client.post(f"/categories/{hobi_id}/rename", data={"new_name": "Hobi & Gaming"})
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    # 2.4 Arsipkan dan Buka Arsip Kategori
    res = client.post(f"/categories/{hobi_id}/archive")
    assert res.status_code == 200
    assert "diarsipkan" in res.text.lower()

    res = client.post(f"/categories/{hobi_id}/unarchive")
    assert res.status_code == 200
    assert "dipulihkan" in res.text.lower()

    # 2.5 Proteksi Kategori Sistem "Biaya Admin"
    with session_scope(factory) as session:
        cats_with_sys = list_categories(session, user.id, include_system=True)
        admin_cat = next(c for c in cats_with_sys if c.is_system)
        admin_cat_id = admin_cat.id

    res = client.post(f"/categories/{admin_cat_id}/rename", data={"new_name": "Admin Bank"})
    assert res.status_code == 200
    assert "sistem" in res.text.lower()

    res = client.post(f"/categories/{admin_cat_id}/archive")
    assert res.status_code == 200
    assert "sistem" in res.text.lower()

    # 2.6 Kelola Metode Pembayaran: Tambah, Rename, Arsipkan, Aktifkan
    res = client.post("/methods/add", data={"name": "Bank Jago"})
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    with session_scope(factory) as session:
        jago = get_method_by_name(session, user.id, "Bank Jago")
        assert jago is not None
        jago_id = jago.id

    res = client.post(f"/methods/{jago_id}/rename", data={"new_name": "Jago Utama"})
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    res = client.post(f"/methods/{jago_id}/archive")
    assert res.status_code == 200
    assert "diarsipkan" in res.text.lower()

    res = client.post(f"/methods/{jago_id}/unarchive")
    assert res.status_code == 200
    assert "dipulihkan" in res.text.lower()

    # 2.7 Kelola Quick Alias
    with session_scope(factory) as session:
        exp_cats = list_categories(session, user.id, type_=TYPE_EXPENSE)
        food_cat = next(c for c in exp_cats if not c.is_system)
        food_cat_id = food_cat.id

    res = client.post(
        "/aliases/add",
        data={"alias": "sate", "target_type": "category", "target_id": food_cat_id},
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    with session_scope(factory) as session:
        rules = aliases_service.list_aliases(session, user.id)
        sate_rule = next(r for r in rules if r.alias == "sate")
        rule_id = sate_rule.id

    res = client.post(f"/aliases/{rule_id}/delete")
    assert res.status_code == 200
    assert "dihapus" in res.text.lower()


# ==============================================================================
# 3. Recurring Transactions
# ==============================================================================


def test_recurring_transactions_crud(web_test_env):
    """Pengujian jadwal transaksi rutin: tambah, aktif/nonaktif, dan hapus."""
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Dewi", 1003)
    _login(client, sent_otps, user)

    # 3.1 Halaman Rutin
    res = client.get("/recurring")
    assert res.status_code == 200
    assert "Transaksi Rutin" in res.text

    with session_scope(factory) as session:
        cats = list_categories(session, user.id, type_=TYPE_EXPENSE)
        cat = next(c for c in cats if not c.is_system)
        methods = list_methods(session, user.id)
        method = methods[0]
        cat_id = cat.id
        method_id = method.id

    # 3.2 Tambah Jadwal Rutin Bulanan
    res = client.post(
        "/recurring/add",
        data={
            "name": "Tagihan WiFi",
            "type": TYPE_EXPENSE,
            "amount": "Rp 150.000",
            "category_id": cat_id,
            "method_id": method_id,
            "frequency": "monthly",
            "day_of_month": 5,
            "start_date": "01-01-2026",
            "note": "Internet WiFi",
        },
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    with session_scope(factory) as session:
        rules = session.query(RecurringTransaction).filter_by(user_id=user.id).all()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.amount == 150_000
        assert rule.is_active is True
        rule_id = rule.id

    # 3.3 Toggle Nonaktif & Aktif
    res = client.post(f"/recurring/{rule_id}/toggle")
    assert res.status_code == 200
    assert "dinonaktifkan" in res.text.lower()

    res = client.post(f"/recurring/{rule_id}/toggle")
    assert res.status_code == 200
    assert "diaktifkan" in res.text.lower()

    # 3.4 Hapus Jadwal Rutin
    res = client.post(f"/recurring/{rule_id}/delete")
    assert res.status_code == 200
    assert "dihapus" in res.text.lower()

    with session_scope(factory) as session:
        rules = session.query(RecurringTransaction).filter_by(user_id=user.id).all()
        assert len(rules) == 0


# ==============================================================================
# 4. Reports, Export (CSV, Excel, PDF), and CSV Import
# ==============================================================================


def test_reports_export_and_import(web_test_env):
    """Pengujian laporan, ekspor CSV (formula protection), Excel, PDF, dan impor CSV."""
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Eko", 1004)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = methods[0]
        cats_exp = list_categories(session, user.id, type_=TYPE_EXPENSE)
        food = next(c for c in cats_exp if not c.is_system)
        cats_inc = list_categories(session, user.id, type_=TYPE_INCOME)
        gaji = cats_inc[0]

        opening_balances_service.set_opening_balance(
            session, user.id, cash.id, 10_000_000, effective_date="01-02-2026"
        )
        tx_service.add_income(
            session, user.id, 5_000_000, gaji.id, cash.id, on="05-02-2026", note="Gaji Pokok"
        )
        # Transaksi biasa dan transaksi dengan karakter berpotensi formula injection
        tx_service.add_expense(
            session, user.id, 50_000, food.id, cash.id, on="10-02-2026", note="=SUM(A1:A5)"
        )
        tx_service.add_expense(
            session, user.id, 25_000, food.id, cash.id, on="10-02-2026", note="Makan Siang"
        )

    # 4.1 Halaman Laporan Berbagai Periode
    for p in ("daily", "weekly", "monthly", "comparison"):
        res = client.get(f"/reports?period={p}&date_str=10-02-2026")
        assert res.status_code == 200
        assert "Laporan Keuangan" in res.text

    # 4.2 Ekspor CSV & Netralisasi Formula Injection
    res_csv = client.get("/reports/export/csv?month=2026-02")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    csv_body = res_csv.text
    # Karakter formula injection diawali petik satu (')
    assert "'=SUM(A1:A5)" in csv_body
    assert "Makan Siang" in csv_body

    # 4.3 Ekspor Excel (.xlsx)
    res_xlsx = client.get("/reports/export/excel?month=2026-02")
    assert res_xlsx.status_code == 200
    assert "spreadsheetml" in res_xlsx.headers["content-type"]
    assert len(res_xlsx.content) > 100

    # 4.4 Ekspor PDF
    res_pdf = client.get("/reports/export/pdf?month=2026-02")
    assert res_pdf.status_code == 200
    assert "application/pdf" in res_pdf.headers["content-type"]
    assert res_pdf.content.startswith(b"%PDF")

    # 4.5 Impor CSV Pratinjau & Deteksi Duplikat
    # Baris 1 duplikat dengan 'Makan Siang 25000' pada 10-02-2026
    # Baris 2 transaksi baru
    csv_content = (
        "Tanggal,Jenis,Kategori,Metode,Nominal,Keterangan\n"
        "10-02-2026,Pengeluaran,Makanan,Cash,25000,Makan Siang Duplikat\n"
        "12-02-2026,Pengeluaran,Makanan,Cash,75000,Belanja Supermarket\n"
    )
    res_prev = client.post(
        "/reports/import/preview",
        files={"file": ("mutasi.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert res_prev.status_code == 200
    assert "Pratinjau Impor Mutasi" in res_prev.text
    assert "Duplikat" in res_prev.text
    assert "75.000" in res_prev.text

    # 4.6 Impor Konfirmasi Simpan
    confirm_payload = {
        "selected_rows": ["1", "2"],
        "date_1": "10-02-2026",
        "type_1": TYPE_EXPENSE,
        "category_id_1": food.id,
        "method_id_1": cash.id,
        "amount_1": 25000,
        "note_1": "Makan Siang Duplikat",
        "date_2": "12-02-2026",
        "type_2": TYPE_EXPENSE,
        "category_id_2": food.id,
        "method_id_2": cash.id,
        "amount_2": 75000,
        "note_2": "Belanja Supermarket",
    }
    res_confirm = client.post("/reports/import/confirm", data=confirm_payload)
    assert res_confirm.status_code == 200
    assert "berhasil" in res_confirm.text.lower() and "transaksi" in res_confirm.text.lower()


# ==============================================================================
# 5. Settings, Balance Consistency, Backup/Restore, and Delete Account
# ==============================================================================


def test_settings_backup_and_delete_account(web_test_env):
    """Pengujian pengaturan zona waktu, audit konsistensi saldo,
    backup/restore ZIP, dan hapus akun.
    """
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Farhan", 1005)
    _login(client, sent_otps, user)

    # 5.1 Halaman Hub /more & /settings
    res = client.get("/more")
    assert res.status_code == 200
    assert "Menu Lainnya" in res.text

    res = client.get("/settings")
    assert res.status_code == 200
    assert "Pengaturan & Keamanan" in res.text
    assert "Asia/Makassar" in res.text

    # 5.2 Simpan Preferensi
    res = client.post(
        "/settings/preferences",
        data={
            "timezone": "Asia/Makassar",
            "daily_time": "21:30",
            "is_daily_active": "true",
        },
    )
    assert res.status_code == 200
    assert "berhasil" in res.text.lower()

    with session_scope(factory) as session:
        u = get_user(session, user.id)
        assert u.timezone == "Asia/Makassar"

    # 5.3 Cek Konsistensi Saldo
    res = client.post("/settings/check-consistency")
    assert res.status_code == 200
    assert "Hasil Audit Konsistensi Saldo" in res.text
    assert "cocok" in res.text.lower()

    # 5.4 Unduh Backup ZIP
    res_backup = client.get("/settings/backup/download")
    assert res_backup.status_code == 200
    assert "application/zip" in res_backup.headers["content-type"]
    zip_bytes = res_backup.content
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()
        assert "manifest.json" in namelist
        assert "categories.csv" in namelist
        assert "payment_methods.csv" in namelist

    # 5.5 Pemulihan Backup (Restore)
    res_restore = client.post(
        "/settings/backup/restore",
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert res_restore.status_code == 200
    assert "dipulihkan" in res_restore.text.lower()

    # 5.6 Hapus Akun: Konfirmasi Salah -> Gagal
    res_del_bad = client.post("/settings/account/delete", data={"confirm_text": "SALAH"})
    assert res_del_bad.status_code == 200
    assert "HAPUS" in res_del_bad.text and "penghapusan akun" in res_del_bad.text

    with session_scope(factory) as session:
        u = get_user(session, user.id)
        assert u is not None

    # 5.7 Hapus Akun: Konfirmasi 'HAPUS' -> Berhasil & Dihapus Permanen
    res_del_ok = client.post(
        "/settings/account/delete",
        data={"confirm_text": "HAPUS"},
        follow_redirects=False,
    )
    assert res_del_ok.status_code == 200
    assert "HX-Redirect" in res_del_ok.headers
    assert "/login" in res_del_ok.headers["HX-Redirect"]

    with session_scope(factory) as session:
        u = get_user(session, user.id)
        assert u is None


# ==============================================================================
# 6. User Isolation in Fase W4 Endpoints
# ==============================================================================


def test_user_isolation_w4(web_test_env):
    """Pengguna A tidak dapat melihat, mengubah, atau menghapus resource Pengguna B."""
    client, factory, sent_otps = web_test_env
    user_a = _create_user(factory, "UserA", 2001)
    user_b = _create_user(factory, "UserB", 2002)

    # Buat kategori kustom & jadwal rutin milik User B
    with session_scope(factory) as session:
        cat_b = categories_service.add_category(session, user_b.id, "Kategori B", TYPE_EXPENSE)
        cat_b_id = cat_b.id
        methods_b = list_methods(session, user_b.id)
        rec_b = RecurringTransaction(
            user_id=user_b.id,
            name="Tagihan B",
            type=TYPE_EXPENSE,
            amount=50_000,
            category_id=cat_b_id,
            method_id=methods_b[0].id,
            frequency="monthly",
            day_of_month=1,
            start_date=today(user_b.timezone),
            is_active=True,
        )
        session.add(rec_b)
        session.flush()
        rec_b_id = rec_b.id

    # Login sebagai User A
    _login(client, sent_otps, user_a)

    # User A mencoba rename atau archive kategori milik User B -> harus ditolak
    res = client.post(f"/categories/{cat_b_id}/rename", data={"new_name": "Bajak Kategori"})
    assert res.status_code == 200
    assert "tidak ditemukan" in res.text.lower() or "error" in res.text.lower()

    res = client.post(f"/categories/{cat_b_id}/archive")
    assert res.status_code == 200
    assert "tidak ditemukan" in res.text.lower() or "error" in res.text.lower()

    # User A mencoba toggle atau delete jadwal rutin milik User B -> harus ditolak
    res = client.post(f"/recurring/{rec_b_id}/toggle")
    assert res.status_code == 200
    assert "tidak ditemukan" in res.text.lower() or "error" in res.text.lower()

    res = client.post(f"/recurring/{rec_b_id}/delete")
    assert res.status_code == 200
    assert "tidak ditemukan" in res.text.lower() or "error" in res.text.lower()

    # Pastikan data User B tetap utuh
    with session_scope(factory) as session:
        c_check = session.get(Category, cat_b_id)
        assert c_check.name == "Kategori B"
        assert c_check.is_archived is False
        r_check = session.get(RecurringTransaction, rec_b_id)
        assert r_check is not None
        assert r_check.is_active is True
