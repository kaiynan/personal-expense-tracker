"""Pengujian unit dan integrasi Web Dashboard Fase W3:
- Halaman riwayat transaksi (/transactions & /history) dan filter dinamis (/transactions/list)
- Modal / Bottom Sheet Transaksi (+): Pengeluaran, Pemasukan, Transfer (+ Biaya Admin)
- Validasi Transfer: penolakan to_method == from_method
- Toast peringatan (orange toast) saat saldo minus atau tanggal sebelum effective_date
- Modal & Form Saldo Awal (/balances/opening) dan Koreksi Saldo (/balances/adjust)
- Penolakan Koreksi Saldo bertanggal sebelum effective_date (Toast Merah)
- Larangan edit/hapus langsung pada transaksi Biaya Admin (related_transaction_id terisi)
- Larangan edit pada transaksi Koreksi Saldo (adjustment)
- Soft Delete transaksi dan pemulihan via tombol Undo (/transactions/undo/{audit_log_id})
- Isolasi data transaksi dan saldo antar pengguna (user_id isolation)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from database.engine import create_db_engine, create_session_factory, session_scope
from database.queries import get_transaction, list_categories, list_methods
from database.seed import seed_defaults
from models import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_TRANSFER,
    Base,
    User,
)
from services import transactions as transactions_service
from utils.dates import format_display, today
from web.app import create_web_app


@pytest.fixture
def web_db(tmp_path):
    """Database SQLite berbasis file sementara untuk pengujian web W3."""
    db_file = tmp_path / "web_w3_test.db"
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
    sent_otps: list[dict[str, int | str]] = []

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
# 1. Halaman Riwayat & Modal Loading Tests
# ==============================================================================


def test_transactions_page_unauthenticated_redirects(web_test_env):
    client, _, _ = web_test_env
    res = client.get("/transactions", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"

    res2 = client.get("/history", follow_redirects=False)
    assert res2.status_code == 303
    assert res2.headers["location"] == "/login"


def test_transactions_page_authenticated_loads_shell(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Budi", 111001)
    _login(client, sent_otps, user)

    res = client.get("/transactions")
    assert res.status_code == 200
    html = res.text
    assert "Riwayat Transaksi" in html
    assert "Daftar & Mutasi Keuangan" in html or "Daftar &amp; Mutasi Keuangan" in html
    assert "Semua Jenis" in html
    assert "transaction-list-container" in html


def test_modal_endpoints_load_partials(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Ani", 111002)
    _login(client, sent_otps, user)

    # Modal Catat Transaksi (+)
    res_tx_modal = client.get("/transactions/modal")
    assert res_tx_modal.status_code == 200
    assert "Catat Transaksi" in res_tx_modal.text
    assert "tx-form-expense" in res_tx_modal.text
    assert "tx-form-income" in res_tx_modal.text
    assert "tx-form-transfer" in res_tx_modal.text
    assert "tx-form-quick" in res_tx_modal.text

    # Modal Pengaturan Saldo
    res_bal_modal = client.get("/balances/modal")
    assert res_bal_modal.status_code == 200
    assert "Pengaturan Saldo" in res_bal_modal.text
    assert "Saldo Awal" in res_bal_modal.text
    assert "Koreksi Saldo" in res_bal_modal.text


# ==============================================================================
# 2. Tambah Pengeluaran, Pemasukan, dan Transfer (+ Biaya Admin)
# ==============================================================================


def test_add_expense_and_income_success(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Citra", 111003)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        gaji = next(c for c in cats if c.name == "Gaji")

    # Catat pengeluaran
    res_exp = client.post(
        "/transactions/expense",
        data={
            "amount": "25.000",
            "category_id": makan.id,
            "method_id": cash.id,
            "note": "Makan siang",
        },
    )
    assert res_exp.status_code == 200
    assert "Pengeluaran Rp25.000 berhasil dicatat" in res_exp.text
    assert res_exp.headers.get("HX-Trigger") == "transactionAdded"

    # Catat pemasukan
    res_inc = client.post(
        "/transactions/income",
        data={
            "amount": "5.000.000",
            "category_id": gaji.id,
            "method_id": cash.id,
            "note": "Bonus bulanan",
        },
    )
    assert res_inc.status_code == 200
    assert "Pemasukan Rp5.000.000 berhasil dicatat" in res_inc.text
    assert res_inc.headers.get("HX-Trigger") == "transactionAdded"


def test_add_transfer_same_method_rejected(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Dedi", 111004)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")

    res = client.post(
        "/transactions/transfer",
        data={
            "amount": "50000",
            "from_method_id": cash.id,
            "to_method_id": cash.id,
        },
    )
    assert res.status_code == 200
    assert "Metode asal dan tujuan transfer tidak boleh sama" in res.text


def test_add_transfer_with_admin_fee_success(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Eka", 111005)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        bank = next(m for m in methods if m.name == "Bank")

    res = client.post(
        "/transactions/transfer",
        data={
            "amount": "100.000",
            "from_method_id": bank.id,
            "to_method_id": cash.id,
            "admin_fee": "2.500",
            "note": "Tarik tunai ATM",
        },
    )
    assert res.status_code == 200
    assert "Transfer Rp100.000 berhasil dicatat" in res.text
    assert "Biaya admin: Rp2.500" in res.text
    assert res.headers.get("HX-Trigger") == "transactionAdded"

    # Verifikasi transaksi transfer dan fee di database
    with session_scope(factory) as session:
        txs = transactions_service.list_transactions(session, user.id)
        transfer_tx = next(t for t in txs if t.type == TYPE_TRANSFER)
        fee_tx = next(t for t in txs if t.related_transaction_id == transfer_tx.id)
        assert transfer_tx.amount == 100_000
        assert fee_tx.amount == 2_500
        assert fee_tx.type == TYPE_EXPENSE


# ==============================================================================
# 3. Toast Warning (Oranye) Saat Saldo Menjadi Minus
# ==============================================================================


def test_warning_toast_when_balance_becomes_negative(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Fajar", 111006)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")

    # Saldo cash awal 0, belanja 50.000 -> saldo jadi -50.000 (warning toast)
    res = client.post(
        "/transactions/expense",
        data={
            "amount": "50.000",
            "category_id": makan.id,
            "method_id": cash.id,
        },
    )
    assert res.status_code == 200
    assert "Pengeluaran Rp50.000 berhasil dicatat" in res.text
    # Harus ada toast warning oranye
    assert "amber" in res.text or "warning" in res.text
    assert "menjadi negatif" in res.text or "minus" in res.text or "Rp-50.000" in res.text


# ==============================================================================
# 4. Saldo Awal dan Koreksi Saldo (Adjustment)
# ==============================================================================


def test_set_opening_balance_and_adjust_balance(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Gita", 111007)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")

    # Set opening balance
    res_open = client.post(
        "/balances/opening",
        data={
            "method_id": cash.id,
            "amount": "500.000",
            "effective_date": "01-01-2026",
        },
    )
    assert res_open.status_code == 200
    assert "Saldo awal Rp500.000 berhasil disimpan" in res_open.text

    # Koreksi saldo menjadi 600.000 per tanggal 15-01-2026 (selisih +100.000)
    res_adj = client.post(
        "/balances/adjust",
        data={
            "method_id": cash.id,
            "actual_balance": "600.000",
            "date": "15-01-2026",
            "note": "Hitung ulang fisik",
        },
    )
    assert res_adj.status_code == 200
    assert "Koreksi saldo sebesar +Rp100.000 berhasil disimpan" in res_adj.text

    # Verifikasi di database
    with session_scope(factory) as session:
        txs = transactions_service.list_transactions(session, user.id, type_=TYPE_ADJUSTMENT)
        assert len(txs) == 1
        assert txs[0].amount == 100_000


def test_adjust_balance_before_effective_date_rejected_with_red_toast(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Hadi", 111008)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")

    # Atur saldo awal efektif per 10-01-2026
    client.post(
        "/balances/opening",
        data={
            "method_id": cash.id,
            "amount": "200.000",
            "effective_date": "10-01-2026",
        },
    )

    # Coba koreksi tanggal 05-01-2026 (sebelum effective date) -> wajib ditolak dengan Toast Merah
    res = client.post(
        "/balances/adjust",
        data={
            "method_id": cash.id,
            "actual_balance": "300.000",
            "date": "05-01-2026",
        },
    )
    assert res.status_code == 200
    # Berisi pesan error dan style toast merah (rose)
    assert "sebelum tanggal berlaku saldo awal" in res.text
    assert "rose" in res.text or "error" in res.text


# ==============================================================================
# 5. Edit, Larangan Edit Fee & Adjustment, dan Soft Delete + Undo
# ==============================================================================


def test_edit_transaction_success(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Indra", 111009)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        tx_res = transactions_service.add_expense(
            session, user.id, amount=15000, category_id=makan.id, method_id=cash.id, note="Bakso"
        )
        tx_id = tx_res.transaction.id

    # Buka modal edit
    res_modal = client.get(f"/transactions/{tx_id}/edit")
    assert res_modal.status_code == 200
    assert f"Edit Transaksi #{tx_id}" in res_modal.text

    # Simpan edit perubahan
    res_edit = client.post(
        f"/transactions/{tx_id}/edit",
        data={
            "amount": "20.000",
            "date": format_display(today(user.timezone)),
            "category_id": makan.id,
            "method_id": cash.id,
            "note": "Bakso + Es Teh",
        },
    )
    assert res_edit.status_code == 200
    assert f"Transaksi #{tx_id} berhasil diperbarui (Rp20.000)" in res_edit.text

    with session_scope(factory) as session:
        updated = get_transaction(session, user.id, tx_id)
        assert updated.amount == 20_000
        assert updated.note == "Bakso + Es Teh"


def test_reject_direct_edit_and_delete_on_admin_fee(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Joko", 111010)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        bank = next(m for m in methods if m.name == "Bank")
        # Buat transfer dengan fee
        client.post(
            "/transactions/transfer",
            data={
                "amount": "50000",
                "from_method_id": bank.id,
                "to_method_id": cash.id,
                "admin_fee": "1500",
            },
        )
        txs = transactions_service.list_transactions(session, user.id)
        fee_tx = next(t for t in txs if t.related_transaction_id is not None)
        fee_id = fee_tx.id

    # Coba get edit modal pada fee
    res_edit_modal = client.get(f"/transactions/{fee_id}/edit")
    assert res_edit_modal.status_code == 200
    assert "biaya admin yang terhubung ke transfer" in res_edit_modal.text

    # Coba delete langsung pada fee
    res_del = client.post(f"/transactions/{fee_id}/delete")
    assert res_del.status_code == 200
    assert "biaya admin yang terhubung ke transfer" in res_del.text


def test_reject_direct_edit_on_adjustment(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Kartika", 111011)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        client.post(
            "/balances/adjust",
            data={"method_id": cash.id, "actual_balance": "100000"},
        )
        txs = transactions_service.list_transactions(session, user.id, type_=TYPE_ADJUSTMENT)
        adj_id = txs[0].id

    res_edit_modal = client.get(f"/transactions/{adj_id}/edit")
    assert res_edit_modal.status_code == 200
    assert "Koreksi saldo tidak bisa diedit" in res_edit_modal.text


def test_soft_delete_and_undo_flow(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Lukman", 111012)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        tx_res = transactions_service.add_expense(
            session, user.id, amount=30000, category_id=makan.id, method_id=cash.id, note="Sate"
        )
        tx_id = tx_res.transaction.id

    # 1. Hapus transaksi
    res_del = client.post(f"/transactions/{tx_id}/delete")
    assert res_del.status_code == 200
    assert f"Transaksi #{tx_id} berhasil dihapus" in res_del.text
    assert "Urungkan (Undo)" in res_del.text
    assert "/transactions/undo/" in res_del.text

    # Verifikasi status transaksi di DB adalah deleted
    with session_scope(factory) as session:
        tx = get_transaction(session, user.id, tx_id, include_deleted=True)
        assert tx.status == STATUS_DELETED

    # Ambil audit_log_id dari respon HTML undo button
    # Contoh format: hx-post="/transactions/undo/123"
    import re

    match = re.search(r"/transactions/undo/(\d+)", res_del.text)
    assert match is not None
    audit_log_id = int(match.group(1))

    # 2. Klik Undo
    res_undo = client.post(f"/transactions/undo/{audit_log_id}")
    assert res_undo.status_code == 200
    assert "Undo sukses" in res_undo.text

    # Verifikasi status transaksi kembali active
    with session_scope(factory) as session:
        tx = get_transaction(session, user.id, tx_id, include_deleted=False)
        assert tx is not None
        assert tx.status == STATUS_ACTIVE


# ==============================================================================
# 6. Filter Interaktif HTMX (/transactions/list)
# ==============================================================================


def test_transactions_filter_partial(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Mega", 111013)
    _login(client, sent_otps, user)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        bank = next(m for m in methods if m.name == "Bank")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        gaji = next(c for c in cats if c.name == "Gaji")

        # Buat 3 transaksi berbeda
        transactions_service.add_expense(
            session,
            user.id,
            amount=15000,
            category_id=makan.id,
            method_id=cash.id,
            note="Kopi Pagi",
        )
        transactions_service.add_income(
            session,
            user.id,
            amount=2000000,
            category_id=gaji.id,
            method_id=bank.id,
            note="Gaji Pokok",
        )

    # 1. Filter type=expense
    res_exp = client.get("/transactions/list?type=expense")
    assert res_exp.status_code == 200
    assert "Kopi Pagi" in res_exp.text
    assert "Gaji Pokok" not in res_exp.text

    # 2. Filter min_amount=100.000
    res_min = client.get("/transactions/list?min_amount=100000")
    assert res_min.status_code == 200
    assert "Gaji Pokok" in res_min.text
    assert "Kopi Pagi" not in res_min.text

    # 3. Filter keyword
    res_kw = client.get("/transactions/list?keyword=Kopi")
    assert res_kw.status_code == 200
    assert "Kopi Pagi" in res_kw.text
    assert "Gaji Pokok" not in res_kw.text


# ==============================================================================
# 7. Isolasi Data Pengguna (User Isolation)
# ==============================================================================


def test_transactions_user_isolation(web_test_env):
    client, factory, sent_otps = web_test_env
    user_a = _create_user(factory, "User A", 111014)
    user_b = _create_user(factory, "User B", 111015)

    with session_scope(factory) as session:
        methods_a = list_methods(session, user_a.id)
        cash_a = next(m for m in methods_a if m.name == "Cash")
        cats_a = list_categories(session, user_a.id)
        makan_a = next(c for c in cats_a if c.name == "Makanan")

        tx_a = transactions_service.add_expense(
            session,
            user_a.id,
            amount=77000,
            category_id=makan_a.id,
            method_id=cash_a.id,
            note="Rahasia A",
        ).transaction
        tx_a_id = tx_a.id

    # Login sebagai User B
    _login(client, sent_otps, user_b)

    # User B tidak boleh melihat transaksi User A di riwayat
    res_list = client.get("/transactions/list")
    assert "Rahasia A" not in res_list.text
    assert "Rp77.000" not in res_list.text

    # User B tidak boleh bisa membuka modal edit transaksi User A
    res_edit_modal = client.get(f"/transactions/{tx_a_id}/edit")
    modal_err = res_edit_modal.text.lower()
    assert "tidak ditemukan" in modal_err or "error" in modal_err

    # User B tidak boleh bisa mengedit transaksi User A
    res_edit = client.post(
        f"/transactions/{tx_a_id}/edit",
        data={"amount": "10000", "category_id": makan_a.id, "method_id": cash_a.id},
    )
    assert "tidak ditemukan" in res_edit.text.lower() or "error" in res_edit.text.lower()

    # User B tidak boleh bisa menghapus transaksi User A
    res_del = client.post(f"/transactions/{tx_a_id}/delete")
    assert "tidak ditemukan" in res_del.text.lower() or "error" in res_del.text.lower()

    # Verifikasi transaksi User A tetap tidak tersentuh di DB
    with session_scope(factory) as session:
        tx_check = get_transaction(session, user_a.id, tx_a_id)
        assert tx_check.amount == 77_000
        assert tx_check.status == STATUS_ACTIVE
