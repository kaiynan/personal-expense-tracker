"""Tes laporan keuangan: batas periode, eksklusi, agregat, dan isolasi pengguna."""

from __future__ import annotations

from datetime import date

import pytest

from database.engine import create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from models import Category, PaymentMethod, User
from reports import (
    format_comparison_text,
    format_report_text,
    format_statistics_text,
    get_daily_report,
    get_financial_statistics,
    get_monthly_comparison,
    get_monthly_report,
    get_period_comparison,
    get_weekly_report,
)
from services import adjustments, transactions, transfers


@pytest.fixture
def repo_ctx(engine):
    """Factory session dan dua user untuk pengujian laporan."""
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        u1 = ensure_default_user(session)
        seed_defaults(session, u1.id)
        u1_id = u1.id

    with session_scope(factory) as session:
        u2 = User(name="User Dua")
        session.add(u2)
        session.flush()
        seed_defaults(session, u2.id)
        u2_id = u2.id

    return factory, u1_id, u2_id


def test_weekly_report_period_boundaries(repo_ctx):
    """Minggu dimulai Senin dan berakhir Minggu. Transaksi di luar rentang dieksklusikan."""
    factory, user_id, _ = repo_ctx

    ref_date = date(2026, 10, 7)

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id

        # 1. Minggu sebelumnya (Minggu 04-10-2026) -> TIDAK MASUK
        transactions.add_expense(session, user_id, 100_000, food_id, cash_id, on="04-10-2026")

        # 2. Hari pertama minggu (Senin 05-10-2026) -> MASUK
        transactions.add_expense(session, user_id, 200_000, food_id, cash_id, on="05-10-2026")

        # 3. Hari di tengah minggu (Rabu 07-10-2026) -> MASUK
        transactions.add_expense(session, user_id, 300_000, food_id, cash_id, on="07-10-2026")

        # 4. Hari terakhir minggu (Minggu 11-10-2026) -> MASUK
        transactions.add_expense(session, user_id, 400_000, food_id, cash_id, on="11-10-2026")

        # 5. Minggu berikutnya (Senin 12-10-2026) -> TIDAK MASUK
        transactions.add_expense(session, user_id, 500_000, food_id, cash_id, on="12-10-2026")

    with session_scope(factory) as session:
        rep = get_weekly_report(session, user_id, on=ref_date)

    assert rep.start_date == date(2026, 10, 5)  # Senin
    assert rep.end_date == date(2026, 10, 11)  # Minggu
    assert rep.total_expense == 900_000  # 200k + 300k + 400k
    assert rep.transaction_count == 3


def test_monthly_report_period_boundaries(repo_ctx):
    """Bulan dimulai tanggal 1 s.d. tanggal terakhir bulan (31 untuk Oktober)."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id

        # 1. Hari terakhir bulan sebelumnya (30-09-2026) -> TIDAK MASUK
        transactions.add_expense(session, user_id, 100_000, food_id, cash_id, on="30-09-2026")

        # 2. Hari pertama bulan (01-10-2026) -> MASUK
        transactions.add_expense(session, user_id, 250_000, food_id, cash_id, on="01-10-2026")

        # 3. Hari terakhir bulan (31-10-2026) -> MASUK
        transactions.add_expense(session, user_id, 350_000, food_id, cash_id, on="31-10-2026")

        # 4. Hari pertama bulan berikutnya (01-11-2026) -> TIDAK MASUK
        transactions.add_expense(session, user_id, 500_000, food_id, cash_id, on="01-11-2026")

    with session_scope(factory) as session:
        rep = get_monthly_report(session, user_id, month="2026-10")

    assert rep.start_date == date(2026, 10, 1)
    assert rep.end_date == date(2026, 10, 31)
    assert rep.total_expense == 600_000  # 250k + 350k
    assert rep.transaction_count == 2


def test_report_excludes_transfers_adjustments_and_deleted(repo_ctx):
    """Transfer, koreksi saldo (adjustment), dan soft delete TIDAK masuk laporan."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        salary_id = session.query(Category).filter_by(user_id=user_id, name="Gaji").one().id
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Pemasukan aktif: 2.000.000
        transactions.add_income(session, user_id, 2_000_000, salary_id, bank_id, on="10-10-2026")

        # Pengeluaran aktif: 500.000
        transactions.add_expense(session, user_id, 500_000, food_id, cash_id, on="10-10-2026")

        # Transfer 1.000.000 (tidak ada biaya admin) -> TIDAK masuk laporan
        transfers.add_transfer(session, user_id, 1_000_000, bank_id, cash_id, on="10-10-2026")

        # Koreksi saldo (adjustment) -> TIDAK masuk laporan
        adjustments.adjust_balance(session, user_id, cash_id, 1_200_000, on="10-10-2026")

        # Pengeluaran yang di-soft-delete -> TIDAK masuk laporan
        del_tx = transactions.add_expense(
            session, user_id, 999_000, food_id, cash_id, on="10-10-2026"
        ).transaction
        transactions.delete_transaction(session, user_id, del_tx.id)

    with session_scope(factory) as session:
        rep = get_monthly_report(session, user_id, month="2026-10")

    assert rep.total_income == 2_000_000
    assert rep.total_expense == 500_000
    assert rep.net == 1_500_000
    assert rep.transaction_count == 2


def test_report_category_breakdown_and_top_expenses(repo_ctx):
    """Pengeluaran per kategori terhitung persentasenya dan top expense terurut nominal terbesar."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        transport_id = (
            session.query(Category).filter_by(user_id=user_id, name="Transportasi").one().id
        )
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id

        # Makanan: 300.000 (3x: 100k, 50k, 150k)
        transactions.add_expense(
            session, user_id, 100_000, food_id, cash_id, on="05-10-2026", note="Makan A"
        )
        transactions.add_expense(
            session, user_id, 50_000, food_id, cash_id, on="06-10-2026", note="Makan B"
        )
        transactions.add_expense(
            session, user_id, 150_000, food_id, cash_id, on="07-10-2026", note="Makan C"
        )

        # Transportasi: 100.000
        transactions.add_expense(
            session, user_id, 100_000, transport_id, cash_id, on="08-10-2026", note="Bensin"
        )

    with session_scope(factory) as session:
        rep = get_weekly_report(session, user_id, on=date(2026, 10, 8))

    assert rep.total_expense == 400_000

    # Rincian kategori
    assert len(rep.category_expenses) == 2
    c_map = {c.category_name: c for c in rep.category_expenses}
    assert c_map["Makanan"].amount == 300_000
    assert c_map["Makanan"].percentage == 75.0
    assert c_map["Transportasi"].amount == 100_000
    assert c_map["Transportasi"].percentage == 25.0

    # Transaksi pengeluaran terbesar
    assert len(rep.top_expenses) == 4
    assert rep.top_expenses[0].amount == 150_000
    assert rep.top_expenses[0].note == "Makan C"

    # Text formatting
    txt = format_report_text(rep)
    assert "LAPORAN MINGGUAN" in txt
    assert "Makanan" in txt
    assert "Rp300.000" in txt
    assert "75.0%" in txt


def test_reports_user_isolation(repo_ctx):
    """Laporan pengguna A tidak mencampur data transaksi pengguna B."""
    factory, u1_id, u2_id = repo_ctx

    with session_scope(factory) as session:
        f1 = session.query(Category).filter_by(user_id=u1_id, name="Makanan").one().id
        c1 = session.query(PaymentMethod).filter_by(user_id=u1_id, name="Cash").one().id

        f2 = session.query(Category).filter_by(user_id=u2_id, name="Makanan").one().id
        c2 = session.query(PaymentMethod).filter_by(user_id=u2_id, name="Cash").one().id

        transactions.add_expense(session, u1_id, 777_000, f1, c1, on="10-10-2026")
        transactions.add_expense(session, u2_id, 222_000, f2, c2, on="10-10-2026")

    with session_scope(factory) as session:
        r1 = get_monthly_report(session, u1_id, month="2026-10")
        r2 = get_monthly_report(session, u2_id, month="2026-10")

    assert r1.total_expense == 777_000
    assert r2.total_expense == 222_000


def test_daily_report_boundaries_and_formatting(repo_ctx):
    """Laporan harian hanya memperhitungkan transaksi pada tanggal yang ditentukan."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        salary_id = session.query(Category).filter_by(user_id=user_id, name="Gaji").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Kemarin (09-10-2026): tidak masuk
        transactions.add_expense(session, user_id, 100_000, food_id, cash_id, on="09-10-2026")

        # Hari ini (10-10-2026): masuk
        transactions.add_income(session, user_id, 1_500_000, salary_id, bank_id, on="10-10-2026")
        transactions.add_expense(session, user_id, 50_000, food_id, cash_id, on="10-10-2026")
        transactions.add_expense(session, user_id, 30_000, food_id, cash_id, on="10-10-2026")

        # Besok (11-10-2026): tidak masuk
        transactions.add_expense(session, user_id, 200_000, food_id, cash_id, on="11-10-2026")

    with session_scope(factory) as session:
        rep = get_daily_report(session, user_id, on="10-10-2026")

    assert rep.period_type == "daily"
    assert rep.start_date == date(2026, 10, 10)
    assert rep.end_date == date(2026, 10, 10)
    assert rep.total_income == 1_500_000
    assert rep.total_expense == 80_000
    assert rep.net == 1_420_000
    assert rep.transaction_count == 3
    assert len(rep.top_expenses) == 2

    # Formatting text
    txt = format_report_text(rep)
    assert "LAPORAN HARIAN (10-10-2026)" in txt
    assert "Tanggal: 10-10-2026" in txt
    assert "Rp1.500.000" in txt
    assert "Rp80.000" in txt


def test_daily_report_empty_and_exclusions(repo_ctx):
    """Hari tanpa transaksi atau hanya transfer/adjustment menghasilkan laporan bersih."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Transfer & adjustment pada 15-10-2026
        transfers.add_transfer(session, user_id, 500_000, bank_id, cash_id, on="15-10-2026")
        adjustments.adjust_balance(session, user_id, cash_id, 1_000_000, on="15-10-2026")

    with session_scope(factory) as session:
        rep = get_daily_report(session, user_id, on="15-10-2026")

    assert rep.total_income == 0
    assert rep.total_expense == 0
    assert rep.net == 0
    assert rep.transaction_count == 0
    assert rep.category_expenses == []
    assert rep.top_expenses == []


def test_period_comparison_calculations_and_deltas(repo_ctx):
    """Perbandingan dua periode menghitung selisih nominal, persentase, dan pergeseran kategori."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        transport_id = (
            session.query(Category).filter_by(user_id=user_id, name="Transportasi").one().id
        )
        salary_id = session.query(Category).filter_by(user_id=user_id, name="Gaji").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Periode 1 (September 2026)
        transactions.add_income(session, user_id, 4_000_000, salary_id, bank_id, on="01-09-2026")
        transactions.add_expense(session, user_id, 500_000, food_id, cash_id, on="05-09-2026")
        transactions.add_expense(session, user_id, 300_000, transport_id, cash_id, on="10-09-2026")

        # Periode 2 (Oktober 2026)
        transactions.add_income(session, user_id, 5_000_000, salary_id, bank_id, on="01-10-2026")
        transactions.add_expense(session, user_id, 700_000, food_id, cash_id, on="05-10-2026")
        transactions.add_expense(session, user_id, 150_000, transport_id, cash_id, on="10-10-2026")

    with session_scope(factory) as session:
        comp = get_monthly_comparison(session, user_id, month1="2026-09", month2="2026-10")

    # Ringkasan periode 1
    assert comp.period1.total_income == 4_000_000
    assert comp.period1.total_expense == 800_000
    assert comp.period1.net == 3_200_000

    # Ringkasan periode 2
    assert comp.period2.total_income == 5_000_000
    assert comp.period2.total_expense == 850_000
    assert comp.period2.net == 4_150_000

    # Selisih (Delta)
    assert comp.income_diff == 1_000_000
    assert comp.income_diff_pct == 25.0
    assert comp.expense_diff == 50_000
    assert comp.expense_diff_pct == 6.2
    assert comp.net_diff == 950_000

    # Kategori deltas
    cat_map = {c.category_name: c for c in comp.category_comparisons}
    assert cat_map["Makanan"].period1_amount == 500_000
    assert cat_map["Makanan"].period2_amount == 700_000
    assert cat_map["Makanan"].diff_amount == 200_000
    assert cat_map["Makanan"].percentage_change == 40.0

    assert cat_map["Transportasi"].period1_amount == 300_000
    assert cat_map["Transportasi"].period2_amount == 150_000
    assert cat_map["Transportasi"].diff_amount == -150_000
    assert cat_map["Transportasi"].percentage_change == -50.0

    # Text formatter
    txt = format_comparison_text(comp)
    assert "PERBANDINGAN PERIODE KEUANGAN" in txt
    assert "Makanan" in txt
    assert "(NAIK)" in txt
    assert "Transportasi" in txt
    assert "(TURUN)" in txt


def test_period_comparison_user_isolation(repo_ctx):
    """Perbandingan periode antar pengguna tidak saling mencampur data transaksi."""
    factory, u1_id, u2_id = repo_ctx

    with session_scope(factory) as session:
        f1 = session.query(Category).filter_by(user_id=u1_id, name="Makanan").one().id
        c1 = session.query(PaymentMethod).filter_by(user_id=u1_id, name="Cash").one().id

        f2 = session.query(Category).filter_by(user_id=u2_id, name="Makanan").one().id
        c2 = session.query(PaymentMethod).filter_by(user_id=u2_id, name="Cash").one().id

        # U1 punya transaksi di Oktober
        transactions.add_expense(session, u1_id, 400_000, f1, c1, on="05-10-2026")

        # U2 punya transaksi di Oktober
        transactions.add_expense(session, u2_id, 900_000, f2, c2, on="05-10-2026")

    with session_scope(factory) as session:
        c_u1 = get_monthly_comparison(session, u1_id, month1="2026-09", month2="2026-10")
        c_u2 = get_monthly_comparison(session, u2_id, month1="2026-09", month2="2026-10")

    assert c_u1.period2.total_expense == 400_000
    assert c_u2.period2.total_expense == 900_000


def test_period_comparison_zero_division_safety(repo_ctx):
    """Jika periode 1 kosong (0), persentase tidak menimbulkan ZeroDivisionError."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id

        # Periode 1 kosong, Periode 2 ada transaksi
        transactions.add_expense(session, user_id, 300_000, food_id, cash_id, on="02-10-2026")

    with session_scope(factory) as session:
        comp = get_period_comparison(
            session, user_id, "01-09-2026", "30-09-2026", "01-10-2026", "31-10-2026"
        )

    assert comp.income_diff_pct is None
    assert comp.expense_diff_pct is None
    assert comp.category_comparisons[0].percentage_change is None
    txt = format_comparison_text(comp)
    assert "(Baru/N/A)" in txt


def test_financial_statistics_calculations(repo_ctx):
    """Statistik keuangan menghitung rata-rata, ekstrem min/max, dan hari aktif belanja."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        food_id = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        bill_id = session.query(Category).filter_by(user_id=user_id, name="Tagihan").one().id
        salary_id = session.query(Category).filter_by(user_id=user_id, name="Gaji").one().id
        cash_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank_id = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Tanggal 01-10-2026 (Gaji + Makanan)
        transactions.add_income(session, user_id, 3_000_000, salary_id, bank_id, on="01-10-2026")
        transactions.add_expense(
            session, user_id, 50_000, food_id, cash_id, on="01-10-2026", note="Sarapan"
        )

        # Tanggal 05-10-2026 (Tagihan besar)
        transactions.add_expense(
            session, user_id, 500_000, bill_id, bank_id, on="05-10-2026", note="Listrik"
        )

        # Tanggal 10-10-2026 (Makanan kecil)
        transactions.add_expense(
            session, user_id, 20_000, food_id, cash_id, on="10-10-2026", note="Kopi"
        )

    with session_scope(factory) as session:
        # Periode 01-10-2026 s.d. 10-10-2026 (10 hari)
        stats = get_financial_statistics(session, user_id, start="01-10-2026", end="10-10-2026")

    assert stats.total_days == 10
    assert stats.spending_days == 3  # Tgl 1, 5, 10
    assert stats.zero_spending_days == 7
    assert stats.total_income == 3_000_000
    assert stats.total_expense == 570_000
    assert stats.net == 2_430_000
    assert stats.income_count == 1
    assert stats.expense_count == 3
    assert stats.total_count == 4

    # Rata-rata nominal (integer rupiah)
    assert stats.avg_daily_expense == 57_000  # 570_000 // 10
    assert stats.avg_daily_income == 300_000  # 3_000_000 // 10
    assert stats.avg_expense_per_tx == 190_000  # 570_000 // 3

    # Nilai ekstrem
    assert stats.max_expense is not None
    assert stats.max_expense.amount == 500_000
    assert stats.max_expense.category_name == "Tagihan"

    assert stats.min_expense is not None
    assert stats.min_expense.amount == 20_000
    assert stats.min_expense.category_name == "Makanan"

    assert stats.max_income is not None
    assert stats.max_income.amount == 3_000_000

    assert stats.max_spending_day is not None
    assert stats.max_spending_day[0] == date(2026, 10, 5)
    assert stats.max_spending_day[1] == 500_000

    # Text formatter
    txt = format_statistics_text(stats)
    assert "STATISTIK KEUANGAN" in txt
    assert "Rp57.000" in txt
    assert "Tagihan" in txt
    assert "Rp500.000" in txt


def test_financial_statistics_empty_period(repo_ctx):
    """Periode kosong tanpa transaksi menghasilkan statistik nilai nol yang aman."""
    factory, user_id, _ = repo_ctx

    with session_scope(factory) as session:
        stats = get_financial_statistics(session, user_id, start="01-01-2026", end="10-01-2026")

    assert stats.total_days == 10
    assert stats.spending_days == 0
    assert stats.zero_spending_days == 10
    assert stats.total_income == 0
    assert stats.total_expense == 0
    assert stats.net == 0
    assert stats.income_count == 0
    assert stats.expense_count == 0
    assert stats.avg_daily_expense == 0
    assert stats.avg_expense_per_tx == 0
    assert stats.max_expense is None
    assert stats.min_expense is None
    assert stats.max_income is None
    assert stats.max_spending_day is None

    txt = format_statistics_text(stats)
    assert "(Tidak ada pengeluaran)" in txt
