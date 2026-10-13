"""Tes unit dan integrasi untuk fitur deteksi pengeluaran tidak wajar / anomali (Fitur V4)."""

import asyncio
from datetime import date
from unittest.mock import patch

from database import queries
from database.engine import create_db_engine, create_session_factory
from database.seed import seed_defaults
from models import TYPE_EXPENSE, Base, User
from services import opening_balances, transactions
from services.anomalies import (
    SEVERITY_EXTREME,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    calculate_quartiles_and_iqr,
    check_expense_anomaly,
    detect_expense_anomaly,
    list_anomalous_transactions,
)


# ==============================================================================
# 1. Tes Fungsi Murni: calculate_quartiles_and_iqr
# ==============================================================================
def test_calculate_quartiles_empty_and_single():
    assert calculate_quartiles_and_iqr([]) == (0, 0, 0, 0)
    assert calculate_quartiles_and_iqr([50_000]) == (50_000, 50_000, 50_000, 0)


def test_calculate_quartiles_even_and_odd():
    # Data: [10, 20, 30, 40]
    # Median = (20 + 30) // 2 = 25
    # Lower half = [10, 20] -> Q1 = 15
    # Upper half = [30, 40] -> Q3 = 35
    # IQR = 35 - 15 = 20
    q1, med, q3, iqr = calculate_quartiles_and_iqr([10, 20, 30, 40])
    assert (q1, med, q3, iqr) == (15, 25, 35, 20)

    # Data: [10, 20, 30, 40, 50]
    # Median = 30
    # Lower half = [10, 20] -> Q1 = 15
    # Upper half = [40, 50] -> Q3 = 45
    # IQR = 45 - 15 = 30
    q1_o, med_o, q3_o, iqr_o = calculate_quartiles_and_iqr([10, 20, 30, 40, 50])
    assert (q1_o, med_o, q3_o, iqr_o) == (15, 30, 45, 30)


# ==============================================================================
# 2. Tes Fungsi Murni: detect_expense_anomaly
# ==============================================================================
def test_detect_expense_anomaly_zero_or_negative():
    res = detect_expense_anomaly(0, [10_000, 20_000, 30_000])
    assert res.is_anomaly is False
    assert res.severity == SEVERITY_NORMAL


def test_detect_expense_anomaly_minimal_history():
    # Riwayat kosong
    res_empty = detect_expense_anomaly(50_000, [])
    assert res_empty.is_anomaly is False
    assert res_empty.sample_size == 0

    # Riwayat hanya 2 data: [20_000, 25_000]
    # Pengeluaran wajar Rp 30.000 -> normal (sampel minim)
    res_small = detect_expense_anomaly(30_000, [20_000, 25_000])
    assert res_small.is_anomaly is False

    # Pengeluaran ekstrem fantastis (Rp 2.000.000 vs rata-rata 22.500) -> warning
    res_huge = detect_expense_anomaly(2_000_000, [20_000, 25_000])
    assert res_huge.is_anomaly is True
    assert res_huge.severity == SEVERITY_WARNING


def test_detect_expense_anomaly_normal_mild_and_extreme():
    # Riwayat belanja makan sehari-hari: Rp 20rb - 30rb
    # [20_000, 22_000, 25_000, 28_000, 30_000]
    history = [20_000, 22_000, 25_000, 28_000, 30_000]
    # Q1 = 21_000, Median = 25_000, Q3 = 29_000, IQR = 8_000
    # mild_upper = 29_000 + 1.5 * 8_000 = 29_000 + 12_000 = 41_000
    # extreme_upper = 29_000 + 3.0 * 8_000 = 29_000 + 24_000 = 53_000

    # 1. Pengeluaran wajar: Rp 35.000 (< 41.000)
    normal_res = detect_expense_anomaly(35_000, history)
    assert normal_res.is_anomaly is False
    assert normal_res.severity == SEVERITY_NORMAL

    # 2. Pengeluaran outlier ringan / warning: Rp 45.000 (>= 41.000, < 53.000)
    warning_res = detect_expense_anomaly(45_000, history)
    assert warning_res.is_anomaly is True
    assert warning_res.severity == SEVERITY_WARNING

    # 3. Pengeluaran outlier ekstrem: Rp 150.000 (>= 53.000)
    extreme_res = detect_expense_anomaly(150_000, history)
    assert extreme_res.is_anomaly is True
    assert extreme_res.severity == SEVERITY_EXTREME
    assert extreme_res.multiplier_of_median == 6.0


def test_detect_expense_anomaly_zero_iqr_fallback():
    # Riwayat seragam (semua bayar kos 500.000)
    history = [500_000, 500_000, 500_000, 500_000]
    # Q1 = 500rb, Q3 = 500rb -> IQR = 0
    # Fallback: mild = 2x median (1.000.000), extreme = 3x median (1.500.000)
    res_normal = detect_expense_anomaly(600_000, history)
    assert res_normal.is_anomaly is False

    res_warning = detect_expense_anomaly(1_100_000, history)
    assert res_warning.is_anomaly is True
    assert res_warning.severity == SEVERITY_WARNING

    res_extreme = detect_expense_anomaly(1_800_000, history)
    assert res_extreme.is_anomaly is True
    assert res_extreme.severity == SEVERITY_EXTREME


# ==============================================================================
# 3. Tes Service: check_expense_anomaly & list_anomalous_transactions
# ==============================================================================
def test_check_expense_anomaly_fallback_to_all_history(session):
    user = User(name="AnomalyUser", status="active")
    session.add(user)
    session.flush()
    seed_defaults(session, user.id)

    cat_food = queries.get_category_by_name(session, user.id, "Makanan", TYPE_EXPENSE)
    cat_bill = queries.get_category_by_name(session, user.id, "Tagihan", TYPE_EXPENSE)
    pms = queries.list_methods(session, user.id)
    method_id = pms[0].id
    opening_balances.set_opening_balance(session, user.id, method_id, 10_000_000, date(2026, 1, 1))

    # Cat food punya 5 transaksi (biasanya ~20.000)
    for amt in (20_000, 22_000, 21_000, 25_000, 23_000):
        transactions.add_expense(session, user.id, amt, cat_food.id, method_id, date(2026, 1, 2))

    # Cek anomali untuk cat_food
    res_food = check_expense_anomaly(session, user.id, 100_000, category_id=cat_food.id)
    assert res_food.is_anomaly is True
    assert res_food.sample_size == 5

    # Untuk cat_bill (belum ada transaksi sama sekali), fallback menggunakan total 5 transaksi user
    res_bill = check_expense_anomaly(session, user.id, 150_000, category_id=cat_bill.id)
    assert res_bill.is_anomaly is True
    assert res_bill.sample_size == 5


def test_list_anomalous_transactions(session):
    user = User(name="InspectorUser", status="active")
    session.add(user)
    session.flush()
    seed_defaults(session, user.id)

    cat = queries.get_category_by_name(session, user.id, "Transportasi", TYPE_EXPENSE)
    pms = queries.list_methods(session, user.id)
    method_id = pms[0].id
    opening_balances.set_opening_balance(session, user.id, method_id, 10_000_000, date(2026, 1, 1))

    # Transaksi normal
    for amt in (15_000, 16_000, 14_000, 15_000, 17_000):
        transactions.add_expense(session, user.id, amt, cat.id, method_id, date(2026, 1, 2))

    # Transaksi anomali
    tx_bad = transactions.add_expense(
        session, user.id, 350_000, cat.id, method_id, date(2026, 1, 3), note="taksi jarak jauh"
    ).transaction

    anomalies = list_anomalous_transactions(session, user.id)
    assert len(anomalies) >= 1
    found_ids = [a.transaction.id for a in anomalies]
    assert tx_bad.id in found_ids


# ==============================================================================
# 4. Tes Konfirmasi Pengguna di CLI saat Deteksi Anomali
# ==============================================================================
def test_cli_add_expense_user_confirms_or_cancels(engine, monkeypatch):
    """Pengguna diberi peringatan jika pengeluaran anomali, dan harus konfirmasi sebelum simpan."""
    from cli import actions

    factory = create_session_factory(engine)
    with factory() as session:
        user = User(name="CliUser", status="active")
        session.add(user)
        session.flush()
        user_id = user.id
        seed_defaults(session, user_id)
        session.commit()

    with (
        patch("cli.actions.prompt_amount", return_value=500_000),
        patch("cli.actions.prompt_choice") as mock_choice,
        patch("cli.actions.prompt_date", return_value=date(2026, 1, 5)),
        patch("cli.actions.prompt_str", return_value="makan mahal"),
        patch("cli.actions.prompt_confirm") as mock_confirm,
        patch("services.anomalies.check_expense_anomaly") as mock_check,
    ):
        from services.anomalies import AnomalyDetectionResult

        mock_check.return_value = AnomalyDetectionResult(
            is_anomaly=True,
            severity=SEVERITY_EXTREME,
            reason="Nominal Rp 500,000 jauh melebihi rata-rata biasanya.",
            sample_size=10,
        )

        # Skenario 1: Pengguna membatalkan konfirmasi (jawab 'n' / False)
        mock_confirm.return_value = False
        from collections import namedtuple

        FakeCat = namedtuple("FakeCat", ["id", "name"])
        FakeMethod = namedtuple("FakeMethod", ["method_id", "name", "balance", "is_archived"])
        mock_choice.side_effect = [
            FakeCat(1, "Makanan"),
            FakeMethod(1, "Cash", 1_000_000, False),
        ]

        with patch("services.transactions.add_expense") as mock_add_exp:
            actions.action_add_expense(factory, user_id)
            # Karena pengguna membatalkan, add_expense TIDAK boleh dipanggil!
            assert mock_add_exp.call_count == 0

        # Skenario 2: Pengguna mengonfirmasi (jawab 'y' / True)
        mock_confirm.return_value = True
        mock_choice.side_effect = [
            FakeCat(1, "Makanan"),
            FakeMethod(1, "Cash", 1_000_000, False),
        ]
        with patch("services.transactions.add_expense") as mock_add_exp:
            from services.results import TransactionResult

            FakeTx = namedtuple("FakeTx", ["id", "date", "amount", "note"])
            mock_add_exp.return_value = TransactionResult(
                transaction=FakeTx(99, date(2026, 1, 5), 500_000, "makan mahal"),
                warnings=[],
            )
            actions.action_add_expense(factory, user_id)
            # Karena pengguna setuju, add_expense dipanggil tepat 1x
            assert mock_add_exp.call_count == 1


# ==============================================================================
# 5. Tes Perintah Telegram Bot: /anomali
# ==============================================================================
def test_bot_cmd_anomali(tmp_path, monkeypatch):
    """Perintah /anomali menampilkan daftar transaksi pengeluaran anomali."""
    import config
    from bot.handlers.common import cmd_start
    from bot.handlers.reports import cmd_anomali
    from tests.test_bot import MockApplication, MockContext, MockUpdate

    tg_id = 998877
    monkeypatch.setattr(config, "ALLOWED_TELEGRAM_IDS", frozenset([tg_id]))

    db_path = tmp_path / "bot_anomali_test.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    app = MockApplication(factory)
    ctx = MockContext(factory, application=app)
    upd_start = MockUpdate(tg_id)
    asyncio.run(cmd_start(upd_start, ctx))

    upd_anomali = MockUpdate(tg_id)
    asyncio.run(cmd_anomali(upd_anomali, ctx))
    assert len(upd_anomali.effective_message.replies) > 0
    reply = upd_anomali.effective_message.replies[0]
    assert (
        "DETEKSI PENGELUARAN TIDAK WAJAR" in reply or "Tidak Ada Pengeluaran Tidak Wajar" in reply
    )
