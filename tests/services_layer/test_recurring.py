"""Unit test untuk service transaksi rutin (services/recurring.py)."""

from datetime import date

import pytest

from models import FREQUENCY_DAILY, FREQUENCY_MONTHLY, FREQUENCY_WEEKLY, FREQUENCY_YEARLY
from services import balances as bal
from services.opening_balances import set_opening_balance
from services.recurring import (
    _calculate_next_due_date,
    _is_recurring_due,
    activate_recurring,
    apply_recurring,
    create_recurring,
    deactivate_recurring,
    delete_recurring,
    get_next_due_date,
    get_recurring,
    list_recurring,
    process_due_recurring,
    update_recurring,
)
from utils.errors import NotFoundError, ValidationError


@pytest.fixture
def user_data(alice):
    u = alice.user
    cat_kos = alice.expense["Kos"]
    cat_net = alice.expense["Tagihan"]
    cat_gaji = alice.income["Gaji"]
    m_bank = alice.methods["Bank"]
    m_cash = alice.methods["Cash"]
    return u, cat_kos, cat_net, cat_gaji, m_bank, m_cash


def test_create_recurring_success(session, user_data):
    u, cat_kos, _, _, m_bank, _ = user_data
    rec = create_recurring(
        session,
        u.id,
        name="  Kos Bulanan  ",
        type_="expense",
        amount=1_500_000,
        category_id=cat_kos.id,
        method_id=m_bank.id,
        frequency="monthly",
        day_of_month=5,
        start_date="01-02-2026",
        note="Kamar 204",
    )
    assert rec.id is not None
    assert rec.name == "Kos Bulanan"
    assert rec.amount == 1_500_000
    assert rec.type == "expense"
    assert rec.frequency == "monthly"
    assert rec.day_of_month == 5
    assert rec.start_date == date(2026, 2, 1)
    assert rec.note == "Kamar 204"
    assert rec.is_active is True
    assert rec.last_generated_date is None


def test_create_recurring_income_success(session, user_data):
    u, _, _, cat_gaji, m_bank, _ = user_data
    rec = create_recurring(
        session,
        u.id,
        name="Gaji Pokok",
        type_="income",
        amount=6_000_000,
        category_id=cat_gaji.id,
        method_id=m_bank.id,
        frequency="monthly",
        day_of_month=25,
        start_date="01-01-2026",
    )
    assert rec.name == "Gaji Pokok"
    assert rec.type == "income"
    assert rec.amount == 6_000_000


def test_create_recurring_validations(session, user_data):
    u, cat_kos, _, cat_gaji, m_bank, _ = user_data

    # Nama kosong
    with pytest.raises(ValidationError, match="Nama transaksi rutin tidak boleh kosong"):
        create_recurring(session, u.id, "   ", "expense", 500_000, cat_kos.id, m_bank.id)

    # Nama terlalu panjang
    with pytest.raises(ValidationError, match="maksimal 100 karakter"):
        create_recurring(session, u.id, "A" * 101, "expense", 500_000, cat_kos.id, m_bank.id)

    # Nominal 0 atau negatif
    with pytest.raises(ValidationError, match="Nominal harus lebih dari 0"):
        create_recurring(session, u.id, "Kos", "expense", 0, cat_kos.id, m_bank.id)

    # Frekuensi tidak valid
    with pytest.raises(ValidationError, match="Frekuensi tidak valid"):
        create_recurring(
            session, u.id, "Kos", "expense", 500_000, cat_kos.id, m_bank.id, frequency="hourly"
        )

    # Tanggal bulanan tidak valid (< 1 atau > 31)
    with pytest.raises(ValidationError, match="antara 1 sampai 31"):
        create_recurring(
            session, u.id, "Kos", "expense", 500_000, cat_kos.id, m_bank.id, day_of_month=32
        )

    # Tipe mismatch dengan kategori
    with pytest.raises(ValidationError, match="bukan kategori expense"):
        create_recurring(session, u.id, "Kos", "expense", 500_000, cat_gaji.id, m_bank.id)

    # Tanggal berakhir mendahului tanggal mulai
    with pytest.raises(ValidationError, match="Tanggal berakhir tidak boleh mendahului"):
        create_recurring(
            session,
            u.id,
            "Kos",
            "expense",
            500_000,
            cat_kos.id,
            m_bank.id,
            day_of_month=1,
            start_date="10-02-2026",
            end_date="01-02-2026",
        )


def test_get_and_list_recurring(session, user_data):
    u, cat_kos, cat_net, _, m_bank, _ = user_data
    r1 = create_recurring(
        session, u.id, "Kos", "expense", 1_000_000, cat_kos.id, m_bank.id, day_of_month=1
    )
    r2 = create_recurring(
        session, u.id, "Internet", "expense", 300_000, cat_net.id, m_bank.id, day_of_month=15
    )
    deactivate_recurring(session, u.id, r2.id)

    assert get_recurring(session, u.id, r1.id).name == "Kos"
    assert get_recurring(session, u.id, 99999) is None

    all_recs = list_recurring(session, u.id)
    assert len(all_recs) == 2

    active_recs = list_recurring(session, u.id, is_active=True)
    assert len(active_recs) == 1
    assert active_recs[0].id == r1.id

    inactive_recs = list_recurring(session, u.id, is_active=False)
    assert len(inactive_recs) == 1
    assert inactive_recs[0].id == r2.id


def test_update_recurring(session, user_data):
    u, cat_kos, cat_net, _, m_bank, m_cash = user_data
    r = create_recurring(
        session, u.id, "Kos", "expense", 1_000_000, cat_kos.id, m_bank.id, day_of_month=1
    )

    updated = update_recurring(
        session,
        u.id,
        r.id,
        name="Kos Baru",
        amount=1_200_000,
        category_id=cat_net.id,
        method_id=m_cash.id,
        day_of_month=10,
        note="Pindah lantai 3",
    )
    assert updated.name == "Kos Baru"
    assert updated.amount == 1_200_000
    assert updated.category_id == cat_net.id
    assert updated.method_id == m_cash.id
    assert updated.day_of_month == 10
    assert updated.note == "Pindah lantai 3"

    with pytest.raises(NotFoundError):
        update_recurring(session, u.id, 99999, name="Ghost")


def test_activate_and_deactivate_recurring(session, user_data):
    u, cat_kos, _, _, m_bank, _ = user_data
    r = create_recurring(
        session, u.id, "Kos", "expense", 1_000_000, cat_kos.id, m_bank.id, day_of_month=1
    )
    assert r.is_active is True

    deactivate_recurring(session, u.id, r.id)
    assert r.is_active is False

    with pytest.raises(ValidationError, match="sudah dalam keadaan nonaktif"):
        deactivate_recurring(session, u.id, r.id)

    activate_recurring(session, u.id, r.id)
    assert r.is_active is True

    with pytest.raises(ValidationError, match="sudah dalam keadaan aktif"):
        activate_recurring(session, u.id, r.id)


def test_delete_recurring(session, user_data):
    u, cat_kos, _, _, m_bank, _ = user_data
    r = create_recurring(
        session, u.id, "Kos", "expense", 1_000_000, cat_kos.id, m_bank.id, day_of_month=1
    )
    delete_recurring(session, u.id, r.id)
    assert get_recurring(session, u.id, r.id) is None

    with pytest.raises(NotFoundError):
        delete_recurring(session, u.id, r.id)


def test_due_date_calculation_frequencies(session, user_data):
    u, cat_kos, _, _, m_bank, _ = user_data

    # 1. Harian
    r_daily = create_recurring(
        session,
        u.id,
        "Kopi",
        "expense",
        20_000,
        cat_kos.id,
        m_bank.id,
        frequency=FREQUENCY_DAILY,
        start_date="01-02-2026",
    )
    assert _calculate_next_due_date(r_daily) == date(2026, 2, 1)
    r_daily.last_generated_date = date(2026, 2, 1)
    assert _calculate_next_due_date(r_daily) == date(2026, 2, 2)

    # 2. Mingguan
    r_weekly = create_recurring(
        session,
        u.id,
        "Laundry",
        "expense",
        35_000,
        cat_kos.id,
        m_bank.id,
        frequency=FREQUENCY_WEEKLY,
        start_date="01-02-2026",
    )
    assert _calculate_next_due_date(r_weekly) == date(2026, 2, 1)
    r_weekly.last_generated_date = date(2026, 2, 1)
    assert _calculate_next_due_date(r_weekly) == date(2026, 2, 8)

    # 3. Bulanan: penyesuaian akhir bulan (clamp 31 di Februari)
    r_monthly_31 = create_recurring(
        session,
        u.id,
        "Internet",
        "expense",
        350_000,
        cat_kos.id,
        m_bank.id,
        frequency=FREQUENCY_MONTHLY,
        day_of_month=31,
        start_date="01-01-2026",
    )
    # Januari tanggal 31
    assert _calculate_next_due_date(r_monthly_31) == date(2026, 1, 31)
    # Setelah Januari 31 dicatat, jatuh tempo berikutnya adalah Februari 28
    # (karena 2026 bukan tahun kabisat)
    r_monthly_31.last_generated_date = date(2026, 1, 31)
    assert _calculate_next_due_date(r_monthly_31) == date(2026, 2, 28)
    # Setelah Februari 28, jatuh tempo berikutnya adalah Maret 31
    r_monthly_31.last_generated_date = date(2026, 2, 28)
    assert _calculate_next_due_date(r_monthly_31) == date(2026, 3, 31)

    # 4. Tahunan
    r_yearly = create_recurring(
        session,
        u.id,
        "Pajak",
        "expense",
        500_000,
        cat_kos.id,
        m_bank.id,
        frequency=FREQUENCY_YEARLY,
        start_date="15-03-2026",
    )
    assert _calculate_next_due_date(r_yearly) == date(2026, 3, 15)
    r_yearly.last_generated_date = date(2026, 3, 15)
    assert _calculate_next_due_date(r_yearly) == date(2027, 3, 15)

    # 5. Melewati end_date
    r_ended = create_recurring(
        session,
        u.id,
        "Promo",
        "expense",
        100_000,
        cat_kos.id,
        m_bank.id,
        frequency=FREQUENCY_MONTHLY,
        day_of_month=1,
        start_date="01-01-2026",
        end_date="01-02-2026",
    )
    r_ended.last_generated_date = date(2026, 2, 1)
    assert _calculate_next_due_date(r_ended) is None
    assert _is_recurring_due(r_ended, date(2026, 3, 1)) is False

    # get_next_due_date via service
    assert get_next_due_date(session, u.id, r_yearly.id) == date(2027, 3, 15)


def test_apply_recurring_expense_and_income(session, user_data):
    u, cat_kos, _, cat_gaji, m_bank, _ = user_data
    set_opening_balance(session, u.id, m_bank.id, 2_000_000, "01-01-2026")
    session.commit()

    r_kos = create_recurring(
        session,
        u.id,
        "Kos Bulanan",
        "expense",
        1_500_000,
        cat_kos.id,
        m_bank.id,
        day_of_month=1,
        note="Kamar 101",
    )

    # Apply manual expense
    res_kos = apply_recurring(session, u.id, r_kos.id, on="01-02-2026")
    tx = res_kos.transaction
    assert tx.amount == 1_500_000
    assert tx.type == "expense"
    assert tx.note == "[Rutin] Kos Bulanan - Kamar 101"
    assert tx.date == date(2026, 2, 1)
    assert r_kos.last_generated_date == date(2026, 2, 1)
    assert bal.get_method_balance(session, u.id, m_bank.id) == 500_000

    # Apply manual income
    r_gaji = create_recurring(
        session,
        u.id,
        "Gaji",
        "income",
        5_000_000,
        cat_gaji.id,
        m_bank.id,
        day_of_month=25,
    )
    res_gaji = apply_recurring(session, u.id, r_gaji.id, on="25-02-2026")
    assert res_gaji.transaction.amount == 5_000_000
    assert res_gaji.transaction.note == "[Rutin] Gaji"
    assert bal.get_method_balance(session, u.id, m_bank.id) == 5_500_000


def test_process_due_recurring(session, user_data):
    u, cat_kos, cat_net, _, m_bank, _ = user_data
    set_opening_balance(session, u.id, m_bank.id, 5_000_000, "01-01-2026")
    session.commit()

    # Kos: jatuh tempo tgl 1
    create_recurring(
        session,
        u.id,
        "Kos",
        "expense",
        1_000_000,
        cat_kos.id,
        m_bank.id,
        day_of_month=1,
        start_date="01-02-2026",
    )
    # Internet: jatuh tempo tgl 15
    create_recurring(
        session,
        u.id,
        "Internet",
        "expense",
        300_000,
        cat_net.id,
        m_bank.id,
        day_of_month=15,
        start_date="01-02-2026",
    )
    # Nonaktif: tidak boleh diproses
    r_off = create_recurring(
        session,
        u.id,
        "Gym",
        "expense",
        200_000,
        cat_kos.id,
        m_bank.id,
        day_of_month=5,
        start_date="01-02-2026",
    )
    deactivate_recurring(session, u.id, r_off.id)

    # Tanggal as_of: 10-02-2026. Hanya Kos (tgl 1) yang jatuh tempo, Internet (tgl 15) belum.
    results = process_due_recurring(session, u.id, as_of="10-02-2026")
    assert len(results) == 1
    assert results[0].transaction.note == "[Rutin] Kos"
    assert bal.get_method_balance(session, u.id, m_bank.id) == 4_000_000

    # Jalankan lagi di tanggal yang sama: tidak menduplikasi karena sudah dicatat
    results_again = process_due_recurring(session, u.id, as_of="10-02-2026")
    assert len(results_again) == 0

    # Tanggal as_of: 20-02-2026. Sekarang Internet (tgl 15) jatuh tempo.
    results_later = process_due_recurring(session, u.id, as_of="20-02-2026")
    assert len(results_later) == 1
    assert results_later[0].transaction.note == "[Rutin] Internet"
    assert bal.get_method_balance(session, u.id, m_bank.id) == 3_700_000
