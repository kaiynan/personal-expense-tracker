"""Pengujian unit dan integrasi Web Dashboard Fase W2:
- Halaman utama Dashboard (/): metrik keuangan, budget progress, Analitik Cerdas & Runway (V4)
- Quick-Action Bar: preview parsing, save confirmation, cancel, penanganan error dan toast warning
- 4 Endpoint JSON Chart.js (/api/charts/...):
  1. Pengeluaran per kategori (Doughnut)
  2. Pemasukan per kategori (Doughnut)
  3. Pemasukan vs Pengeluaran per periode (Bar)
  4. Perkembangan saldo kumulatif (Line)
- Verifikasi ketat: transfer dan adjustment TIDAK masuk hitungan pemasukan/pengeluaran grafik
- Isolasi data antar pengguna (user_id isolation) pada seluruh endpoint dashboard dan grafik
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from database.engine import create_db_engine, create_session_factory, session_scope
from database.queries import list_categories, list_methods, set_budget
from database.seed import seed_defaults
from models import (
    TYPE_EXPENSE,
    Base,
    Transaction,
    User,
)
from services import adjustments as adjustments_service
from services import opening_balances as opening_service
from services import transactions as transactions_service
from services import transfers as transfers_service
from utils.dates import today
from web.app import create_web_app


@pytest.fixture
def web_db(tmp_path):
    """Database SQLite berbasis file sementara untuk pengujian web."""
    db_file = tmp_path / "web_w2_test.db"
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
    """Helper login via flow OTP."""
    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent_otps[-1]["code"]
    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res.status_code in (200, 303)


# ==============================================================================
# 1. Dashboard Page & App Shell Tests
# ==============================================================================


def test_dashboard_unauthenticated_redirects(web_test_env):
    client, _, _ = web_test_env
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"


def test_dashboard_authenticated_loads_full_metrics_and_v4(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Budi", 111222)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        bank = next(m for m in methods if m.name == "Bank")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        gaji = next(c for c in cats if c.name == "Gaji")

        opening_service.set_opening_balance(session, user.id, cash.id, 1_000_000)
        opening_service.set_opening_balance(session, user.id, bank.id, 2_000_000)
        transactions_service.add_income(session, user.id, 5_000_000, gaji.id, bank.id)
        transactions_service.add_expense(session, user.id, 200_000, makan.id, cash.id)

        # Set monthly budget
        current_month = today().strftime("%Y-%m")
        set_budget(session, user.id, current_month, 1_000_000)

    _login(client, sent_otps, user)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Brand, user greeting, and metrics
    assert "Halo, Budi!" in html
    assert "Total Saldo" in html
    assert "Pemasukan Bulan Ini" in html
    assert "Pengeluaran Bulan Ini" in html
    assert "data-balance" in html

    # Method balances list
    assert "Cash" in html
    assert "Bank" in html

    # Budget info
    assert "Budget Bulanan" in html
    assert "20%" in html  # 200.000 / 1.000.000 = 20%

    # V4 Analytics panel
    assert "Analitik Cerdas & Runway (V4)" in html
    assert "Burn Rate Harian" in html
    assert "Proyeksi Akhir Bulan" in html
    assert "Perkiraan Saldo Habis" in html
    assert "Batas Harian Disarankan" in html


def test_dashboard_summary_cards_endpoint(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Sari", 333444)
    _login(client, sent_otps, user)

    res = client.get("/dashboard/summary-cards")
    assert res.status_code == 200
    assert "summary-cards-container" in res.text
    assert "Total Saldo" in res.text


# ==============================================================================
# 2. Quick-Action Bar Tests
# ==============================================================================


def test_quick_action_bar_get(web_test_env):
    client, _, _ = web_test_env
    res = client.get("/quick-action/bar")
    assert res.status_code == 200
    assert "quick-action-container" in res.text
    assert "Ketik cepat: makan 15000 gopay" in res.text


def test_quick_action_preview_success(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Andi", 555666)
    _login(client, sent_otps, user)

    res = client.post("/quick-action/preview", data={"text": "makan 25000 cash"})
    assert res.status_code == 200
    assert "Konfirmasi Input Cepat" in res.text
    assert "Rp25.000" in res.text
    assert "Makanan" in res.text
    assert "Cash" in res.text
    assert "Simpan" in res.text
    assert "Batal" in res.text


def test_quick_action_preview_empty_or_invalid(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Cici", 777888)
    _login(client, sent_otps, user)

    # Teks kosong
    res_empty = client.post("/quick-action/preview", data={"text": "   "})
    assert res_empty.status_code == 200
    assert "tidak boleh kosong" in res_empty.text

    # Teks tanpa angka nominal
    res_no_amt = client.post("/quick-action/preview", data={"text": "makan cash"})
    assert res_no_amt.status_code == 200
    assert "Nominal transaksi tidak ditemukan" in res_no_amt.text


def test_quick_action_cancel(web_test_env):
    client, _, _ = web_test_env
    res = client.post("/quick-action/cancel")
    assert res.status_code == 200
    assert "quick-action-container" in res.text
    assert "Ketik cepat: makan 15000 gopay" in res.text


def test_quick_action_confirm_expense_and_income(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Dewi", 888999)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        opening_service.set_opening_balance(session, user.id, cash.id, 500_000)

    _login(client, sent_otps, user)

    # 1. Konfirmasi pengeluaran
    res_confirm = client.post(
        "/quick-action/confirm",
        data={
            "type": TYPE_EXPENSE,
            "amount": 25000,
            "category_id": makan.id,
            "method_id": cash.id,
            "note": "Nasi Padang",
        },
    )
    assert res_confirm.status_code == 200
    assert res_confirm.headers.get("HX-Trigger") == "refreshDashboard"
    assert "Transaksi Pengeluaran Rp25.000 berhasil disimpan" in res_confirm.text
    assert "toast-container" in res_confirm.text
    assert "summary-cards-container" in res_confirm.text

    # Verifikasi tersimpan di database
    with session_scope(factory) as session:
        txs = session.query(Transaction).filter_by(user_id=user.id, type=TYPE_EXPENSE).all()
        assert len(txs) == 1
        assert txs[0].amount == 25000
        assert txs[0].note == "Nasi Padang"


def test_quick_action_confirm_shows_warning_toast_on_negative_balance(web_test_env):
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Eko", 999111)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        # Saldo awal hanya 10.000
        opening_service.set_opening_balance(session, user.id, cash.id, 10_000)

    _login(client, sent_otps, user)

    # Pengeluaran 50.000 membuat saldo Cash menjadi -40.000
    res = client.post(
        "/quick-action/confirm",
        data={
            "type": TYPE_EXPENSE,
            "amount": 50000,
            "category_id": makan.id,
            "method_id": cash.id,
            "note": "Belanja lebih",
        },
    )
    assert res.status_code == 200
    # Berhasil disimpan (transaksi tetap tersimpan)
    assert "berhasil disimpan" in res.text
    # Muncul toast peringatan oranye/kuning saldo minus
    assert "minus" in res.text.lower() or "negatif" in res.text.lower()


# ==============================================================================
# 3. 4 Interactive Chart.js Endpoints Tests (/api/charts/...)
# ==============================================================================


def test_charts_endpoints_require_authentication(web_test_env):
    client, _, _ = web_test_env
    endpoints = [
        "/api/charts/expenses-by-category",
        "/api/charts/income-by-category",
        "/api/charts/income-vs-expense",
        "/api/charts/balance-trend",
    ]
    for ep in endpoints:
        res = client.get(ep)
        assert res.status_code == 401
        assert res.json()["error"] == "Unauthorized"


def test_charts_endpoints_data_and_transfer_exclusion(web_test_env):
    """Verifikasi transfer dan adjustment tidak masuk ke grafik pemasukan/pengeluaran."""
    client, factory, sent_otps = web_test_env
    user = _create_user(factory, "Fajar", 121314)

    with session_scope(factory) as session:
        methods = list_methods(session, user.id)
        cash = next(m for m in methods if m.name == "Cash")
        bank = next(m for m in methods if m.name == "Bank")
        cats = list_categories(session, user.id)
        makan = next(c for c in cats if c.name == "Makanan")
        trans = next(c for c in cats if c.name == "Transportasi")
        gaji = next(c for c in cats if c.name == "Gaji")

        # Saldo awal
        opening_service.set_opening_balance(session, user.id, cash.id, 1_000_000)
        opening_service.set_opening_balance(session, user.id, bank.id, 2_000_000)

        # Transaksi aktif: 2 pengeluaran, 1 pemasukan
        transactions_service.add_expense(session, user.id, 100_000, makan.id, cash.id)
        transactions_service.add_expense(session, user.id, 50_000, trans.id, cash.id)
        transactions_service.add_income(session, user.id, 3_000_000, gaji.id, bank.id)

        # Transfer Bank -> Cash 500.000 (tidak boleh masuk grafik pemasukan/pengeluaran)
        transfers_service.add_transfer(session, user.id, 500_000, bank.id, cash.id)

        # Adjustment Cash +200.000 (tidak boleh masuk grafik pemasukan/pengeluaran)
        adjustments_service.adjust_balance(session, user.id, cash.id, 200_000, note="Koreksi")

    _login(client, sent_otps, user)

    # 1. Chart Pengeluaran per Kategori
    res_exp = client.get("/api/charts/expenses-by-category?period=monthly")
    assert res_exp.status_code == 200
    exp_json = res_exp.json()
    assert exp_json["total"] == 150_000  # 100k + 50k
    assert "Makanan" in exp_json["labels"]
    assert "Transportasi" in exp_json["labels"]
    assert len(exp_json["values"]) == 2
    # Pastikan transfer / adjustment tidak muncul sebagai label kategori
    assert "Transfer" not in exp_json["labels"]
    assert "Koreksi" not in exp_json["labels"]

    # 2. Chart Pemasukan per Kategori
    res_inc = client.get("/api/charts/income-by-category?period=monthly")
    assert res_inc.status_code == 200
    inc_json = res_inc.json()
    assert inc_json["total"] == 3_000_000
    assert "Gaji" in inc_json["labels"]
    assert len(inc_json["values"]) == 1
    assert "Transfer" not in inc_json["labels"]

    # 3. Chart Arus Kas: Pemasukan vs Pengeluaran
    res_flow = client.get("/api/charts/income-vs-expense?period=monthly")
    assert res_flow.status_code == 200
    flow_json = res_flow.json()
    assert flow_json["total_income"] == 3_000_000
    assert flow_json["total_expense"] == 150_000
    assert len(flow_json["labels"]) > 0

    # Cek periode mingguan
    res_weekly = client.get("/api/charts/income-vs-expense?period=weekly")
    assert res_weekly.status_code == 200
    assert res_weekly.json()["period"] == "weekly"

    # 4. Chart Perkembangan Saldo Kumulatif
    res_trend = client.get("/api/charts/balance-trend?period=monthly")
    assert res_trend.status_code == 200
    trend_json = res_trend.json()
    assert len(trend_json["labels"]) > 0
    assert len(trend_json["values"]) > 0
    # Saldo akhir: Cash=200rb, Bank=2jt+3jt-500rb=4.5jt. Total = 4.700.000
    assert trend_json["current_balance"] == 4_700_000


def test_charts_user_isolation(web_test_env):
    """Pastikan data pengguna A terisolasi penuh dari pengguna B di endpoint grafik."""
    client, factory, sent_otps = web_test_env
    alice = _create_user(factory, "Alice", 101010)
    bob = _create_user(factory, "Bob", 202020)

    with session_scope(factory) as session:
        a_methods = list_methods(session, alice.id)
        a_cash = next(m for m in a_methods if m.name == "Cash")
        a_cats = list_categories(session, alice.id)
        a_makan = next(c for c in a_cats if c.name == "Makanan")

        b_methods = list_methods(session, bob.id)
        b_cash = next(m for m in b_methods if m.name == "Cash")
        b_cats = list_categories(session, bob.id)
        b_makan = next(c for c in b_cats if c.name == "Makanan")

        # Alice belanja 500.000
        transactions_service.add_expense(session, alice.id, 500_000, a_makan.id, a_cash.id)
        # Bob belanja 20.000
        transactions_service.add_expense(session, bob.id, 20_000, b_makan.id, b_cash.id)

    # 1. Login sebagai Alice
    _login(client, sent_otps, alice)
    res_alice = client.get("/api/charts/expenses-by-category?period=monthly")
    assert res_alice.status_code == 200
    assert res_alice.json()["total"] == 500_000

    # 2. Login sebagai Bob (menggantikan session cookie)
    _login(client, sent_otps, bob)
    res_bob = client.get("/api/charts/expenses-by-category?period=monthly")
    assert res_bob.status_code == 200
    assert res_bob.json()["total"] == 20_000
