"""Tes unit untuk service pengingat harian dan ringkasan otomatis (V2)."""

from datetime import date

import pytest

from models import (
    FREQUENCY_DAILY,
    REMINDER_DAILY,
    REMINDER_MONTHLY,
    REMINDER_WEEKLY,
)
from services import (
    budgets,
    recurring,
    reminders,
)
from services import (
    transactions as tx_svc,
)
from utils.errors import NotFoundError, ValidationError


def test_get_and_update_reminder_settings(session, alice):
    """Pengambilan dan pembaruan pengaturan pengingat."""
    # 1. Ambil setting awal (default)
    setting = reminders.get_reminder_settings(session, alice.id)
    assert setting.user_id == alice.id
    assert setting.is_daily_active is True
    assert setting.daily_time == "20:00"
    assert setting.is_weekly_active is True
    assert setting.is_monthly_active is True

    # 2. Update dengan data valid
    updated = reminders.update_reminder_settings(
        session,
        alice.id,
        is_daily_active=False,
        daily_time="07:15",
        is_weekly_active=False,
        is_monthly_active=True,
    )
    assert updated.is_daily_active is False
    assert updated.daily_time == "07:15"
    assert updated.is_weekly_active is False
    assert updated.is_monthly_active is True

    # 3. Validasi format jam tidak valid
    invalid_times = ["7:15", "25:00", "12:60", "abcde", "12-00", "", "   "]
    for bad_time in invalid_times:
        with pytest.raises(ValidationError):
            reminders.update_reminder_settings(session, alice.id, daily_time=bad_time)


def test_update_reminder_settings_not_found(session):
    """Update setting untuk user yang tidak ada menghasilkan NotFoundError."""
    with pytest.raises(NotFoundError):
        reminders.update_reminder_settings(session, 999999, daily_time="08:00")


def test_mark_reminder_sent(session, alice):
    """Menandai tanggal notifikasi pengingat terkirim."""
    d1 = date(2026, 2, 10)
    d2 = date(2026, 2, 11)
    d3 = date(2026, 2, 12)

    # Daily
    rs = reminders.mark_reminder_sent(session, alice.id, REMINDER_DAILY, d1)
    assert rs.last_daily_sent == d1

    # Weekly
    rs = reminders.mark_reminder_sent(session, alice.id, REMINDER_WEEKLY, d2)
    assert rs.last_weekly_sent == d2

    # Monthly
    rs = reminders.mark_reminder_sent(session, alice.id, REMINDER_MONTHLY, d3)
    assert rs.last_monthly_sent == d3

    # Invalid reminder type
    with pytest.raises(ValidationError):
        reminders.mark_reminder_sent(session, alice.id, "yearly", d1)


def test_build_daily_reminder_no_transactions(session, alice):
    """Pengingat harian saat pengguna belum mencatat transaksi sama sekali hari ini."""
    today_date = date(2026, 3, 1)
    payload = reminders.build_daily_reminder(session, alice.id, as_of=today_date)

    assert payload.user_id == alice.id
    assert payload.date == today_date
    assert payload.has_transactions is False
    assert payload.transactions_count == 0
    assert payload.total_expense == 0
    assert payload.total_income == 0
    assert "Belum Ada Transaksi" in payload.message


def test_build_daily_reminder_with_activity_recurring_and_budget(session, alice):
    """Pengingat harian dengan transaksi hari ini, recurring due, dan budget warning."""
    target_date = date(2026, 3, 15)

    # 1. Catat transaksi hari ini
    alice.open(alice.cash, 1_000_000, effective=date(2026, 1, 1))
    tx_svc.add_expense(session, alice.id, 45_000, alice.food, alice.cash, on=target_date)
    tx_svc.add_income(session, alice.id, 200_000, alice.salary, alice.cash, on=target_date)

    # 2. Tambah transaksi rutin jatuh tempo
    recurring.create_recurring(
        session,
        alice.id,
        name="Langganan Cloud",
        type_="expense",
        amount=100_000,
        category_id=alice.food,
        method_id=alice.cash,
        frequency=FREQUENCY_DAILY,
        start_date=date(2026, 3, 1),
    )

    # 3. Tambah budget yang terlampaui
    budgets.set_category_budget(
        session,
        alice.id,
        alice.food,
        40_000,  # spent 45.000 > 40.000 (100%+)
        month="2026-03",
    )

    payload = reminders.build_daily_reminder(session, alice.id, as_of=target_date)

    assert payload.has_transactions is True
    assert payload.transactions_count == 2
    assert payload.total_expense == 45_000
    assert payload.total_income == 200_000
    assert len(payload.due_recurring) >= 1
    assert any("Langganan Cloud" in r[0] for r in payload.due_recurring)
    assert len(payload.budget_warnings) >= 1
    assert "Rp45.000" in payload.message
    assert "Langganan Cloud" in payload.message


def test_build_weekly_summary(session, alice):
    """Pembuatan ringkasan mingguan otomatis."""
    alice.open(alice.cash, 1_000_000, effective=date(2026, 1, 1))
    # Tanggal di minggu 2 s.d. 8 Februari 2026
    tx_svc.add_income(session, alice.id, 500_000, alice.salary, alice.cash, on=date(2026, 2, 2))
    tx_svc.add_expense(session, alice.id, 120_000, alice.food, alice.cash, on=date(2026, 2, 4))

    summary = reminders.build_weekly_summary(session, alice.id, as_of=date(2026, 2, 5))

    assert summary.user_id == alice.id
    assert summary.start_date == date(2026, 2, 2)
    assert summary.end_date == date(2026, 2, 8)
    assert summary.total_income == 500_000
    assert summary.total_expense == 120_000
    assert summary.net_cash_flow == 380_000
    assert summary.transaction_count == 2
    assert summary.top_category_name == "Makanan"
    assert summary.top_category_amount == 120_000
    assert "RINGKASAN KEUANGAN MINGGUAN" in summary.message
    assert "Rp120.000" in summary.message


def test_build_monthly_summary_and_v2_insights(session, alice):
    """Pembuatan ringkasan bulanan otomatis beserta insight keuangan V2."""
    alice.open(alice.cash, 5_000_000, effective=date(2026, 1, 1))

    # Transaksi bulan lalu (Januari 2026)
    tx_svc.add_expense(session, alice.id, 100_000, alice.food, alice.cash, on=date(2026, 1, 15))

    # Transaksi bulan ini (Februari 2026)
    # Total expense di Makanan 280.000 (naik 180.000 dari Januari)
    tx_svc.add_expense(session, alice.id, 280_000, alice.food, alice.cash, on=date(2026, 2, 10))
    tx_svc.add_income(session, alice.id, 1_000_000, alice.salary, alice.cash, on=date(2026, 2, 5))

    # As of 14 Februari 2026 (day 14 of 28 in Feb 2026)
    summary = reminders.build_monthly_summary(session, alice.id, as_of=date(2026, 2, 14))

    assert summary.user_id == alice.id
    assert summary.month == "2026-02"
    assert summary.total_income == 1_000_000
    assert summary.total_expense == 280_000
    assert summary.net_cash_flow == 720_000
    assert summary.transaction_count == 2

    # Insight 1: Rata-rata per hari (280.000 // 14 = 20.000)
    assert summary.average_daily_expense == 20_000

    # Insight 2: Proyeksi akhir bulan (280.000 * 28 // 14 = 560.000)
    assert summary.projected_monthly_expense == 560_000

    # Insight 3: Kategori yang naik dibanding bulan lalu
    assert len(summary.categories_with_increase) >= 1
    food_inc = next((c for c in summary.categories_with_increase if c[0] == "Makanan"), None)
    assert food_inc is not None
    assert food_inc[1] == 280_000  # pengeluaran bulan ini
    assert food_inc[2] == 180_000  # kenaikan dibanding bulan lalu

    # Format message
    assert "RINGKASAN & INSIGHT KEUANGAN BULANAN" in summary.message
    assert "Rata-rata Pengeluaran/Hari: `Rp20.000`" in summary.message
    assert "Proyeksi Pengeluaran Akhir Bulan: `Rp560.000`" in summary.message
    assert "Makanan" in summary.message


def test_v3_user_timezone_settings_wib_wita_wit(session, alice, bob):
    """Pengaturan zona waktu WIB/WITA/WIT per pengguna tersimpan di user_settings dan users."""
    from database import queries

    # Default awal: Asia/Jakarta (WIB)
    us_alice = reminders.get_user_settings(session, alice.id)
    assert us_alice.timezone == "Asia/Jakarta"

    # Ubah Alice ke WITA, Bob ke WIT
    us_alice = reminders.set_user_timezone(session, alice.id, "WITA")
    us_bob = reminders.set_user_timezone(session, bob.id, "WIT")
    assert us_alice.timezone == "Asia/Makassar"
    assert us_bob.timezone == "Asia/Jayapura"
    assert queries.get_user(session, alice.id).timezone == "Asia/Makassar"
    assert queries.get_user(session, bob.id).timezone == "Asia/Jayapura"

    # Ubah kembali ke WIB
    us_alice = reminders.set_user_timezone(session, alice.id, "wib")
    assert us_alice.timezone == "Asia/Jakarta"

    # Zona waktu tidak valid ditolak
    with pytest.raises(ValidationError):
        reminders.set_user_timezone(session, alice.id, "Zona/TidakAda")


def test_day_and_period_boundaries_at_date_rollover_two_timezones(session, alice, bob, monkeypatch):
    """Batas hari, minggu, dan bulan di pergantian tanggal untuk dua zona waktu berbeda."""
    from datetime import UTC, datetime

    import utils.dates as dates_mod
    from reports import get_daily_report, get_monthly_report, get_weekly_report

    # Alice di WIB (Asia/Jakarta, UTC+7), Bob di WIT (Asia/Jayapura, UTC+9)
    reminders.set_user_timezone(session, alice.id, "WIB")
    reminders.set_user_timezone(session, bob.id, "WIT")

    # Skenario 1: Pergantian tanggal & bulan
    # Waktu UTC: 2026-02-28 16:30:00 UTC
    # -> WIB (UTC+7): Sabtu, 28 Februari 2026 pukul 23:30 (masih Februari!)
    # -> WIT (UTC+9): Minggu, 1 Maret 2026 pukul 01:30 (sudah Maret!)
    fixed_utc_month_rollover = datetime(2026, 2, 28, 16, 30, 0, tzinfo=UTC)
    monkeypatch.setattr(dates_mod, "_utcnow", lambda: fixed_utc_month_rollover)

    assert dates_mod.today("WIB") == date(2026, 2, 28)
    assert dates_mod.today("WIT") == date(2026, 3, 1)

    # Catat pengeluaran tanpa parameter tanggal eksplisit (otomatis memakai "hari ini" user)
    alice.open(alice.cash, 1_000_000, effective=date(2026, 2, 1))
    bob.open(bob.cash, 1_000_000, effective=date(2026, 2, 1))
    tx_alice = tx_svc.add_expense(
        session, alice.id, 50_000, alice.food, alice.cash, note="Makan malam WIB"
    ).transaction
    tx_bob = tx_svc.add_expense(
        session, bob.id, 75_000, bob.food, bob.cash, note="Makan tengah malam WIT"
    ).transaction

    assert tx_alice.date == date(2026, 2, 28)
    assert tx_bob.date == date(2026, 3, 1)

    # Laporan harian default ("hari ini") masing-masing mengikuti zona waktu pengguna
    rep_daily_alice = get_daily_report(session, alice.id)
    rep_daily_bob = get_daily_report(session, bob.id)
    assert rep_daily_alice.start_date == date(2026, 2, 28)
    assert rep_daily_alice.total_expense == 50_000
    assert rep_daily_bob.start_date == date(2026, 3, 1)
    assert rep_daily_bob.total_expense == 75_000

    # Laporan bulanan default ("bulan ini") berbeda bulan karena pergantian tanggal
    rep_month_alice = get_monthly_report(session, alice.id)
    rep_month_bob = get_monthly_report(session, bob.id)
    assert rep_month_alice.start_date == date(2026, 2, 1)
    assert rep_month_alice.end_date == date(2026, 2, 28)
    assert rep_month_alice.total_expense == 50_000
    assert rep_month_bob.start_date == date(2026, 3, 1)
    assert rep_month_bob.end_date == date(2026, 3, 31)
    assert rep_month_bob.total_expense == 75_000

    # Skenario 2: Pergantian minggu (Minggu malam WIB vs Senin dini hari WIT)
    # Waktu UTC: 2026-03-01 16:30:00 UTC
    # -> WIB (UTC+7): Minggu, 1 Maret 2026 pukul 23:30 (minggu 23 Feb - 1 Mar 2026)
    # -> WIT (UTC+9): Senin, 2 Maret 2026 pukul 01:30 (minggu baru 2 Mar - 8 Mar 2026)
    fixed_utc_week_rollover = datetime(2026, 3, 1, 16, 30, 0, tzinfo=UTC)
    monkeypatch.setattr(dates_mod, "_utcnow", lambda: fixed_utc_week_rollover)

    rep_week_alice = get_weekly_report(session, alice.id)
    rep_week_bob = get_weekly_report(session, bob.id)
    assert rep_week_alice.start_date == date(2026, 2, 23)
    assert rep_week_alice.end_date == date(2026, 3, 1)
    assert rep_week_bob.start_date == date(2026, 3, 2)
    assert rep_week_bob.end_date == date(2026, 3, 8)


def test_collect_due_notifications_and_sent_notifications_idempotency(session, alice):
    """Notifikasi terjadwal bersifat idempoten dan menghormati saklar aktif/nonaktif."""
    # Senin, 1 Juni 2026 adalah hari Senin (weekly) sekaligus tanggal 1 (monthly)
    monday_first = date(2026, 6, 1)

    recurring.create_recurring(
        session,
        alice.id,
        name="Tagihan Internet",
        type_="expense",
        amount=300_000,
        category_id=alice.food,
        method_id=alice.cash,
        frequency=FREQUENCY_DAILY,
        start_date=monday_first,
    )

    # Semua jenis notifikasi aktif -> 4 notifikasi jatuh tempo (daily, recurring, weekly, monthly)
    due1 = reminders.collect_due_notifications(session, alice.id, as_of=monday_first)
    types1 = [n.notification_type for n in due1]
    assert types1 == ["daily", "recurring", "weekly", "monthly"]

    # Panggilan ulang pada periode yang sama (mis. setelah bot restart) -> kosong (idempoten!)
    due2 = reminders.collect_due_notifications(session, alice.id, as_of=monday_first)
    assert due2 == []

    # Pada hari berikutnya (2 Juni 2026), jika pengguna mematikan daily & recurring -> kosong
    reminders.update_user_settings(
        session, alice.id, is_daily_active=False, is_recurring_active=False
    )
    due3 = reminders.collect_due_notifications(session, alice.id, as_of=date(2026, 6, 2))
    assert due3 == []
