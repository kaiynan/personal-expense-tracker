"""Tes unit runway, burn rate, proyeksi, dan peringatan prediktif (Fitur V4)."""

from datetime import date

import pytest

from models import (
    NOTIFICATION_PREDICTIVE_RUNWAY,
    User,
)
from services.reminders import collect_predictive_notifications
from services.runway import (
    PREDICTIVE_THRESH_BUDGET_EARLY_EXHAUST,
    PREDICTIVE_THRESH_BUDGET_PROJECTED_EXCEEDED,
    PREDICTIVE_THRESH_RUNWAY_CRITICAL,
    calculate_balance_runway,
    evaluate_predictive_alerts,
    evaluate_runway_metrics,
)


# ==============================================================================
# 1. Hari pertama bulan (d = 1)
# ==============================================================================
def test_first_day_of_month_with_no_expense():
    """Hari pertama bulan tanpa transaksi (total_expense = 0)."""
    d = date(2026, 10, 1)  # Oktober: 31 hari
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=0,
        total_balance=5_000_000,
        budget_amount=3_100_000,
    )
    assert metrics.elapsed_days == 1
    assert metrics.days_in_month == 31
    assert metrics.remaining_days_inclusive == 31
    assert metrics.daily_burn_rate == 0
    assert metrics.projected_monthly_spend == 0
    assert metrics.budget_exhaustion_date is None
    assert metrics.is_budget_exceeded is False
    assert metrics.balance_runway_days is None
    assert metrics.balance_exhaustion_date is None
    # Batas harian disarankan: 3_100_000 // 31 = 100_000
    assert metrics.recommended_daily_limit == 100_000


def test_first_day_of_month_with_single_transaction():
    """Hari pertama bulan dengan satu transaksi awal (mis. Rp 100.000)."""
    d = date(2026, 10, 1)  # Oktober: 31 hari
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=100_000,
        total_balance=3_000_000,
        budget_amount=3_100_000,
    )
    assert metrics.elapsed_days == 1
    assert metrics.daily_burn_rate == 100_000
    # Proyeksi: (100_000 * 31) // 1 = 3_100_000
    assert metrics.projected_monthly_spend == 3_100_000
    # Sisa budget: 3_000_000. Hari tersisa: 3_000_000 // 100_000 = 30 hari -> 1 Okt + 30 = 31 Okt
    assert metrics.budget_exhaustion_date == date(2026, 10, 31)
    assert metrics.budget_days_left == 30
    # Runway saldo: 3_000_000 // 100_000 = 30 hari -> habis 31 Okt
    assert metrics.balance_runway_days == 30
    assert metrics.balance_exhaustion_date == date(2026, 10, 31)
    # Batas harian disarankan: (3_100_000 - 100_000) // 31 = 96_774
    assert metrics.recommended_daily_limit == 96_774


# ==============================================================================
# 2. Data minim (mis. transaksi kecil di awal / pertengahan)
# ==============================================================================
def test_minimal_data_middle_of_month():
    """Data minim: pengeluaran kecil Rp 30.000 pada hari ke-3 di bulan 30 hari."""
    d = date(2026, 4, 3)  # April: 30 hari
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=30_000,
        total_balance=1_000_000,
        budget_amount=900_000,
    )
    assert metrics.elapsed_days == 3
    assert metrics.daily_burn_rate == 10_000  # 30_000 // 3
    assert metrics.projected_monthly_spend == 300_000  # (30_000 * 30) // 3
    # Sisa budget: 870_000 // 10_000 = 87 hari -> 3 Apr + 87 hari
    assert metrics.budget_days_left == 87
    assert metrics.budget_exhaustion_date == date(2026, 6, 29)
    assert metrics.balance_runway_days == 100  # 1_000_000 // 10_000


# ==============================================================================
# 3. Bulan dengan panjang 28, 30, dan 31 hari (serta kabisat 29 hari)
# ==============================================================================
@pytest.mark.parametrize(
    ("eval_date", "expected_days"),
    [
        (date(2026, 2, 14), 28),  # Februari 2026: 28 hari (bukan kabisat)
        (date(2024, 2, 14), 29),  # Februari 2024: 29 hari (kabisat)
        (date(2026, 4, 15), 30),  # April 2026: 30 hari
        (date(2026, 11, 15), 30),  # November 2026: 30 hari
        (date(2026, 1, 15), 31),  # Januari 2026: 31 hari
        (date(2026, 12, 15), 31),  # Desember 2026: 31 hari
    ],
)
def test_month_lengths_and_projection(eval_date, expected_days):
    """Pengecekan perhitungan bulan 28, 29, 30, dan 31 hari."""
    expense = 140_000  # Rp 10.000 / hari jika dievaluasi hari ke-14
    metrics = evaluate_runway_metrics(
        as_of=eval_date,
        total_expense=expense,
        total_balance=500_000,
        budget_amount=500_000,
    )
    assert metrics.days_in_month == expected_days
    burn = expense // eval_date.day
    assert metrics.daily_burn_rate == burn
    assert metrics.projected_monthly_spend == (expense * expected_days) // eval_date.day


# ==============================================================================
# 4. Budget yang sudah terlampaui
# ==============================================================================
def test_budget_already_exceeded():
    """Kasus budget sudah terlampaui (total_expense >= budget_amount)."""
    d = date(2026, 10, 15)
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=1_200_000,
        total_balance=800_000,
        budget_amount=1_000_000,
    )
    assert metrics.is_budget_exceeded is True
    assert metrics.budget_exhaustion_date == d
    assert metrics.budget_days_left == 0
    assert metrics.budget_remaining == -200_000
    assert metrics.recommended_daily_limit == 0

    # Cek alert: harus menghasilkan alert projected exceeded
    alerts = evaluate_predictive_alerts(metrics)
    thresh_codes = [a.threshold_code for a in alerts]
    assert PREDICTIVE_THRESH_BUDGET_PROJECTED_EXCEEDED in thresh_codes


# ==============================================================================
# 5. Bulan tanpa transaksi
# ==============================================================================
def test_month_without_transactions():
    """Kasus bulan tanpa transaksi sama sekali di pertengahan bulan."""
    d = date(2026, 10, 20)  # Hari ke-20
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=0,
        total_balance=2_000_000,
        budget_amount=1_500_000,
    )
    assert metrics.daily_burn_rate == 0
    assert metrics.projected_monthly_spend == 0
    assert metrics.budget_exhaustion_date is None
    assert metrics.balance_runway_days is None
    assert metrics.balance_exhaustion_date is None
    # Sisa hari: 31 - 20 + 1 = 12 hari. Batas: 1_500_000 // 12 = 125_000
    assert metrics.recommended_daily_limit == 125_000

    # Tidak boleh ada peringatan bahaya/alert
    alerts = evaluate_predictive_alerts(metrics)
    assert alerts == []


# ==============================================================================
# 6. Saldo kas habis atau minus
# ==============================================================================
def test_balance_depleted_or_negative():
    """Kasus saldo kas Rp 0 atau negatif."""
    d = date(2026, 10, 10)
    runway_days, bal_date, is_ex = calculate_balance_runway(
        total_balance=0, burn_rate=50_000, as_of=d
    )
    assert runway_days == 0
    assert bal_date == d
    assert is_ex is True

    runway_days_neg, bal_date_neg, is_ex_neg = calculate_balance_runway(
        total_balance=-50_000, burn_rate=50_000, as_of=d
    )
    assert runway_days_neg == 0
    assert bal_date_neg == d
    assert is_ex_neg is True


# ==============================================================================
# 7. Evaluasi ambang batas peringatan prediktif
# ==============================================================================
def test_predictive_alerts_thresholds():
    """Uji berbagai pemicu ambang batas peringatan prediktif."""
    d = date(2026, 10, 10)  # Hari ke-10 dari 31 hari
    # Budget 1.000.000, pengeluaran 400.000 (burn rate 40.000/hari)
    # Proyeksi akhir bulan: (400.000 * 31) // 10 = 1.240.000 (melebihi 100%)
    # Sisa budget: 600.000 // 40.000 = 15 hari -> habis 25 Oktober (sebelum 31 Okt!)
    # Saldo 240.000 -> runway: 240.000 // 40.000 = 6 hari (kritis <= 7)
    metrics = evaluate_runway_metrics(
        as_of=d,
        total_expense=400_000,
        total_balance=240_000,
        budget_amount=1_000_000,
    )
    alerts = evaluate_predictive_alerts(metrics)
    thresh_codes = {a.threshold_code for a in alerts}

    assert PREDICTIVE_THRESH_BUDGET_PROJECTED_EXCEEDED in thresh_codes
    assert PREDICTIVE_THRESH_BUDGET_EARLY_EXHAUST in thresh_codes
    assert PREDICTIVE_THRESH_RUNWAY_CRITICAL in thresh_codes


# ==============================================================================
# 8. Idempotensi pengiriman peringatan prediktif per bulan
# ==============================================================================
def test_predictive_notification_idempotence(session):
    """Peringatan prediktif hanya dikirim 1x per ambang per bulan."""
    user = User(name="PredictiveUser", telegram_id=99991, status="active", timezone="Asia/Jakarta")
    session.add(user)
    session.flush()

    d1 = date(2026, 10, 10)
    # Panggilan pertama menghasilkan notifikasi jika ada alert yang terpicu
    # (Di sini saldo 0, jadi runway_critical akan terpicu)
    notifs1 = collect_predictive_notifications(session, user.id, as_of=d1)
    # Karena saldo awal 0, runway habis -> terpicu runway_critical
    assert len(notifs1) >= 1
    assert any(n.notification_type == NOTIFICATION_PREDICTIVE_RUNWAY for n in notifs1)

    # Panggilan kedua di bulan dan ambang yang sama: idempoten (0 item baru)
    notifs2 = collect_predictive_notifications(session, user.id, as_of=d1)
    assert notifs2 == []

    # Panggilan di hari lain pada bulan yang sama: tetap idempoten
    d2 = date(2026, 10, 15)
    notifs3 = collect_predictive_notifications(session, user.id, as_of=d2)
    assert notifs3 == []

    # Panggilan di bulan berikutnya (November 2026): dikirim kembali (karena periode baru)
    d_next_month = date(2026, 11, 1)
    notifs_next = collect_predictive_notifications(session, user.id, as_of=d_next_month)
    assert len(notifs_next) >= 1
