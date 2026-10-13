"""Laporan keuangan mingguan dan bulanan.

Hanya menghitung pemasukan dan pengeluaran aktif (transfer dan adjustment tidak masuk).
Menggunakan query agregat di database untuk efisiensi memori.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from database.reports import (
    get_category_breakdown,
    get_daily_spending_breakdown,
    get_detailed_period_summary,
    get_extreme_transactions,
    get_period_summary,
    get_top_expenses,
)
from models import TYPE_EXPENSE
from services.budgets import _resolve_month
from services.common import require_user
from utils.dates import format_display, today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date


@dataclass(frozen=True)
class CategoryExpenseItem:
    """Rincian pengeluaran per kategori."""

    category_id: int
    category_name: str
    amount: int
    percentage: float


@dataclass(frozen=True)
class TopExpenseItem:
    """Satu transaksi pengeluaran terbesar."""

    id: int
    date: date
    category_name: str
    amount: int
    note: str | None


@dataclass(frozen=True)
class FinancialReport:
    """Struktur data lengkap laporan keuangan."""

    period_type: str  # "daily", "weekly", atau "monthly"
    title: str
    start_date: date
    end_date: date
    total_income: int
    total_expense: int
    net: int
    transaction_count: int
    category_expenses: list[CategoryExpenseItem]
    top_expenses: list[TopExpenseItem]


def get_daily_report(
    session: Session,
    user_id: int,
    on: date | str | None = None,
) -> FinancialReport:
    """Buat laporan keuangan harian (1 hari).

    Args:
        on: tanggal patokan (date atau teks DD-MM-YYYY). Default hari ini.
    """
    user = require_user(session, user_id)
    ref_date = parse_date(on, "Tanggal patokan") if on is not None else today(user.timezone)

    title = f"Laporan Harian ({format_display(ref_date)})"
    return _build_report(session, user_id, "daily", title, ref_date, ref_date)


def get_weekly_report(
    session: Session,
    user_id: int,
    on: date | str | None = None,
) -> FinancialReport:
    """Buat laporan mingguan (Senin s.d. Minggu).

    Args:
        on: tanggal patokan (date atau teks DD-MM-YYYY). Default hari ini.
    """
    user = require_user(session, user_id)
    ref_date = parse_date(on, "Tanggal patokan") if on is not None else today(user.timezone)

    # Minggu dimulai hari Senin (weekday 0) dan berakhir hari Minggu (weekday 6)
    start_date = ref_date - timedelta(days=ref_date.weekday())
    end_date = start_date + timedelta(days=6)

    title = f"Laporan Mingguan ({format_display(start_date)} s.d. {format_display(end_date)})"
    return _build_report(session, user_id, "weekly", title, start_date, end_date)


def get_monthly_report(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> FinancialReport:
    """Buat laporan bulanan (tanggal 1 s.d. akhir bulan).

    Args:
        month: teks YYYY-MM. Default bulan ini.
    """
    user = require_user(session, user_id)
    month_str = _resolve_month(month, user.timezone)
    year, month_num = (int(x) for x in month_str.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = date(year, month_num, 1)
    end_date = date(year, month_num, last_day)

    title = f"Laporan Bulanan ({month_str})"
    return _build_report(session, user_id, "monthly", title, start_date, end_date)


def _build_report(
    session: Session,
    user_id: int,
    period_type: str,
    title: str,
    start_date: date,
    end_date: date,
) -> FinancialReport:
    """Kompilasi laporan dengan memanggil query agregat database."""
    income, expense, count = get_period_summary(session, user_id, start_date, end_date)
    raw_breakdown = get_category_breakdown(session, user_id, TYPE_EXPENSE, start_date, end_date)
    raw_top = get_top_expenses(session, user_id, start_date, end_date, limit=5)

    category_items = [
        CategoryExpenseItem(
            category_id=cid,
            category_name=cname,
            amount=amt,
            percentage=round((amt / expense) * 100, 1) if expense > 0 else 0.0,
        )
        for cid, cname, amt in raw_breakdown
    ]

    top_items = [
        TopExpenseItem(
            id=tid,
            date=tdate,
            category_name=cname,
            amount=amt,
            note=note,
        )
        for tid, tdate, cname, amt, note in raw_top
    ]

    return FinancialReport(
        period_type=period_type,
        title=title,
        start_date=start_date,
        end_date=end_date,
        total_income=income,
        total_expense=expense,
        net=income - expense,
        transaction_count=count,
        category_expenses=category_items,
        top_expenses=top_items,
    )


def format_report_text(report: FinancialReport) -> str:
    """Format laporan keuangan menjadi teks terminal ASCII."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"  {report.title.upper()}")
    if report.start_date == report.end_date:
        lines.append(f"  Tanggal: {format_display(report.start_date)}")
    else:
        lines.append(
            f"  Periode: {format_display(report.start_date)} s.d. {format_display(report.end_date)}"
        )
    lines.append("=" * 60)

    # 1. Ringkasan Arus Kas
    lines.append("\n[RINGKASAN ARUS KAS]")
    lines.append(f"  Total Pemasukan   : {format_rupiah(report.total_income)}")
    lines.append(f"  Total Pengeluaran : {format_rupiah(report.total_expense)}")
    lines.append("  " + "-" * 32)
    net_sign = "+" if report.net > 0 else ""
    lines.append(f"  Selisih (Net)     : {net_sign}{format_rupiah(report.net)}")
    lines.append(f"  Jumlah Transaksi  : {report.transaction_count} transaksi")

    # 2. Pengeluaran per Kategori
    lines.append("\n[PENGELUARAN PER KATEGORI]")
    if report.category_expenses:
        max_cat = max(len(c.category_name) for c in report.category_expenses)
        for c in report.category_expenses:
            lines.append(
                f"  * {c.category_name.ljust(max_cat)} : "
                f"{format_rupiah(c.amount).rjust(14)} ({c.percentage:5.1f}%)"
            )
    else:
        lines.append("  (Tidak ada pengeluaran pada periode ini)")

    # 3. Transaksi Pengeluaran Terbesar
    lines.append("\n[TRANSAKSI PENGELUARAN TERBESAR]")
    if report.top_expenses:
        for idx, item in enumerate(report.top_expenses, start=1):
            note_str = f" - {item.note}" if item.note else ""
            lines.append(
                f"  {idx}. #{item.id} [{format_display(item.date)}] "
                f"{item.category_name}: {format_rupiah(item.amount)}{note_str}"
            )
    else:
        lines.append("  (Tidak ada transaksi pengeluaran)")

    lines.append("=" * 60)
    return "\n".join(lines)


# --- PERBANDINGAN PERIODE ---


@dataclass(frozen=True)
class CategoryComparisonItem:
    """Perbandingan pengeluaran per kategori antar dua periode."""

    category_id: int
    category_name: str
    period1_amount: int
    period2_amount: int
    diff_amount: int  # period2_amount - period1_amount
    percentage_change: float | None  # ((p2 - p1) / p1) * 100 jika p1 > 0


@dataclass(frozen=True)
class PeriodSummary:
    """Ringkasan keuangan satu periode."""

    label: str
    start_date: date
    end_date: date
    total_income: int
    total_expense: int
    net: int
    transaction_count: int


@dataclass(frozen=True)
class PeriodComparisonReport:
    """Laporan perbandingan antara dua periode keuangan."""

    period1: PeriodSummary
    period2: PeriodSummary
    income_diff: int
    income_diff_pct: float | None
    expense_diff: int
    expense_diff_pct: float | None
    net_diff: int
    category_comparisons: list[CategoryComparisonItem]


def get_period_comparison(
    session: Session,
    user_id: int,
    p1_start: date | str,
    p1_end: date | str,
    p2_start: date | str,
    p2_end: date | str,
    p1_label: str = "Periode 1",
    p2_label: str = "Periode 2",
) -> PeriodComparisonReport:
    """Bandingkan dua periode keuangan pengguna.

    Args:
        p1_start, p1_end: batas tanggal periode 1.
        p2_start, p2_end: batas tanggal periode 2.
        p1_label, p2_label: label deskriptif masing-masing periode.
    """
    require_user(session, user_id)
    d1_start = parse_date(p1_start, "Awal periode 1") if isinstance(p1_start, str) else p1_start
    d1_end = parse_date(p1_end, "Akhir periode 1") if isinstance(p1_end, str) else p1_end
    d2_start = parse_date(p2_start, "Awal periode 2") if isinstance(p2_start, str) else p2_start
    d2_end = parse_date(p2_end, "Akhir periode 2") if isinstance(p2_end, str) else p2_end

    if d1_start > d1_end:
        raise ValidationError("Tanggal awal periode 1 tidak boleh setelah tanggal akhir")
    if d2_start > d2_end:
        raise ValidationError("Tanggal awal periode 2 tidak boleh setelah tanggal akhir")

    # 1. Ringkasan masing-masing periode
    inc1, exp1, cnt1 = get_period_summary(session, user_id, d1_start, d1_end)
    p1 = PeriodSummary(
        label=p1_label,
        start_date=d1_start,
        end_date=d1_end,
        total_income=inc1,
        total_expense=exp1,
        net=inc1 - exp1,
        transaction_count=cnt1,
    )

    inc2, exp2, cnt2 = get_period_summary(session, user_id, d2_start, d2_end)
    p2 = PeriodSummary(
        label=p2_label,
        start_date=d2_start,
        end_date=d2_end,
        total_income=inc2,
        total_expense=exp2,
        net=inc2 - exp2,
        transaction_count=cnt2,
    )

    # 2. Selisih
    income_diff = inc2 - inc1
    income_diff_pct = round(((inc2 - inc1) / inc1) * 100, 1) if inc1 > 0 else None

    expense_diff = exp2 - exp1
    expense_diff_pct = round(((exp2 - exp1) / exp1) * 100, 1) if exp1 > 0 else None

    net_diff = (inc2 - exp2) - (inc1 - exp1)

    # 3. Kategori perbandingan
    raw_cats1 = get_category_breakdown(session, user_id, TYPE_EXPENSE, d1_start, d1_end)
    raw_cats2 = get_category_breakdown(session, user_id, TYPE_EXPENSE, d2_start, d2_end)

    cats1 = {cid: (cname, amt) for cid, cname, amt in raw_cats1}
    cats2 = {cid: (cname, amt) for cid, cname, amt in raw_cats2}

    all_cat_ids = set(cats1.keys()) | set(cats2.keys())
    cat_items: list[CategoryComparisonItem] = []
    for cid in all_cat_ids:
        cname1, amt1 = cats1.get(cid, ("", 0))
        cname2, amt2 = cats2.get(cid, ("", 0))
        name = cname2 or cname1
        diff = amt2 - amt1
        pct = round((diff / amt1) * 100, 1) if amt1 > 0 else None
        cat_items.append(
            CategoryComparisonItem(
                category_id=cid,
                category_name=name,
                period1_amount=amt1,
                period2_amount=amt2,
                diff_amount=diff,
                percentage_change=pct,
            )
        )

    cat_items.sort(key=lambda x: (x.period2_amount, x.period1_amount), reverse=True)

    return PeriodComparisonReport(
        period1=p1,
        period2=p2,
        income_diff=income_diff,
        income_diff_pct=income_diff_pct,
        expense_diff=expense_diff,
        expense_diff_pct=expense_diff_pct,
        net_diff=net_diff,
        category_comparisons=cat_items,
    )


def get_monthly_comparison(
    session: Session,
    user_id: int,
    month1: str | None = None,
    month2: str | None = None,
) -> PeriodComparisonReport:
    """Bandingkan dua bulan (format YYYY-MM).

    Default: month2 adalah bulan ini, month1 adalah bulan sebelumnya.
    """
    user = require_user(session, user_id)
    m2_str = _resolve_month(month2, user.timezone)
    y2, m2_num = (int(x) for x in m2_str.split("-"))

    if month1 is None:
        if m2_num == 1:
            y1, m1_num = y2 - 1, 12
        else:
            y1, m1_num = y2, m2_num - 1
        m1_str = f"{y1:04d}-{m1_num:02d}"
    else:
        m1_str = _resolve_month(month1, user.timezone)
        y1, m1_num = (int(x) for x in m1_str.split("-"))

    _, last_day1 = calendar.monthrange(y1, m1_num)
    _, last_day2 = calendar.monthrange(y2, m2_num)

    d1_start = date(y1, m1_num, 1)
    d1_end = date(y1, m1_num, last_day1)
    d2_start = date(y2, m2_num, 1)
    d2_end = date(y2, m2_num, last_day2)

    return get_period_comparison(
        session,
        user_id,
        d1_start,
        d1_end,
        d2_start,
        d2_end,
        p1_label=f"Bulan {m1_str}",
        p2_label=f"Bulan {m2_str}",
    )


def format_comparison_text(report: PeriodComparisonReport) -> str:
    """Format perbandingan periode menjadi teks terminal ASCII."""
    lines: list[str] = []
    p1, p2 = report.period1, report.period2
    lines.append("=" * 65)
    lines.append("  PERBANDINGAN PERIODE KEUANGAN")
    lines.append(
        f"  {p1.label}: {format_display(p1.start_date)} s.d. {format_display(p1.end_date)}"
    )
    lines.append(
        f"  {p2.label}: {format_display(p2.start_date)} s.d. {format_display(p2.end_date)}"
    )
    lines.append("=" * 65)

    def _pct_str(pct: float | None) -> str:
        if pct is None:
            return "(Baru/N/A)"
        sign = "+" if pct > 0 else ""
        return f"({sign}{pct:+.1f}%)" if pct < 0 else f"({sign}{pct:.1f}%)"

    def _diff_sign(val: int) -> str:
        if val > 0:
            return f"+{format_rupiah(val)}"
        elif val < 0:
            return f"-{format_rupiah(abs(val))}"
        return format_rupiah(0)

    # 1. Ringkasan Total
    lines.append("\n[RINGKASAN ARUS KAS]")
    lines.append(
        f"  Total Pemasukan   : {format_rupiah(p1.total_income)} -> "
        f"{format_rupiah(p2.total_income)}"
    )
    lines.append(
        f"    Perubahan       : {_diff_sign(report.income_diff)} {_pct_str(report.income_diff_pct)}"
    )

    lines.append(
        f"  Total Pengeluaran : {format_rupiah(p1.total_expense)} -> "
        f"{format_rupiah(p2.total_expense)}"
    )
    lines.append(
        f"    Perubahan       : {_diff_sign(report.expense_diff)} "
        f"{_pct_str(report.expense_diff_pct)}"
    )

    lines.append(f"  Selisih (Net)     : {format_rupiah(p1.net)} -> {format_rupiah(p2.net)}")
    lines.append(f"    Perubahan       : {_diff_sign(report.net_diff)}")

    lines.append(
        f"  Jumlah Transaksi  : {p1.transaction_count} -> {p2.transaction_count} transaksi"
    )

    # 2. Perubahan per Kategori Pengeluaran
    lines.append("\n[PERUBAHAN PENGELUARAN PER KATEGORI]")
    if report.category_comparisons:
        max_len = max(len(c.category_name) for c in report.category_comparisons)
        for c in report.category_comparisons:
            trend = "NAIK" if c.diff_amount > 0 else "TURUN" if c.diff_amount < 0 else "SAMA"
            diff_txt = _diff_sign(c.diff_amount)
            pct_txt = _pct_str(c.percentage_change)
            lines.append(
                f"  * {c.category_name.ljust(max_len)}: "
                f"{format_rupiah(c.period1_amount)} -> {format_rupiah(c.period2_amount)} "
                f"[{diff_txt} {pct_txt}] ({trend})"
            )
    else:
        lines.append("  (Tidak ada pengeluaran pada kedua periode)")

    lines.append("=" * 65)
    return "\n".join(lines)


# --- STATISTIK KEUANGAN ---


@dataclass(frozen=True)
class FinancialStatistics:
    """Struktur data statistik dan insight keuangan."""

    title: str
    start_date: date
    end_date: date
    total_days: int
    spending_days: int
    zero_spending_days: int
    total_income: int
    total_expense: int
    net: int
    income_count: int
    expense_count: int
    total_count: int
    avg_daily_expense: int
    avg_daily_income: int
    avg_expense_per_tx: int
    max_expense: TopExpenseItem | None
    min_expense: TopExpenseItem | None
    max_income: TopExpenseItem | None
    max_spending_day: tuple[date, int] | None
    projected_month_expense: int | None
    category_expenses: list[CategoryExpenseItem]


def get_financial_statistics(
    session: Session,
    user_id: int,
    start: date | str | None = None,
    end: date | str | None = None,
    month: str | None = None,
) -> FinancialStatistics:
    """Hitung statistik dan insight keuangan dalam suatu periode.

    Jika `month` diberikan (YYYY-MM), rentang adalah 1 s.d. akhir bulan tersebut.
    Jika tidak ada argumen tanggal, default adalah bulan berjalan.
    """
    user = require_user(session, user_id)
    today_date = today(user.timezone)

    if month is not None:
        m_str = _resolve_month(month, user.timezone)
        y, m_num = (int(x) for x in m_str.split("-"))
        _, last_day = calendar.monthrange(y, m_num)
        start_date = date(y, m_num, 1)
        end_date = date(y, m_num, last_day)
        is_monthly_view = True
    elif start is not None and end is not None:
        start_date = parse_date(start, "Tanggal awal") if isinstance(start, str) else start
        end_date = parse_date(end, "Tanggal akhir") if isinstance(end, str) else end
        is_monthly_view = False
    else:
        m_str = f"{today_date.year:04d}-{today_date.month:02d}"
        _, last_day = calendar.monthrange(today_date.year, today_date.month)
        start_date = date(today_date.year, today_date.month, 1)
        end_date = date(today_date.year, today_date.month, last_day)
        is_monthly_view = True

    if start_date > end_date:
        raise ValidationError("Tanggal awal tidak boleh setelah tanggal akhir")

    total_days = (end_date - start_date).days + 1

    # Agregat ringkasan dan kategori
    inc, exp, inc_cnt, exp_cnt = get_detailed_period_summary(session, user_id, start_date, end_date)
    raw_breakdown = get_category_breakdown(session, user_id, TYPE_EXPENSE, start_date, end_date)
    extremes = get_extreme_transactions(session, user_id, start_date, end_date)
    daily_expenses = get_daily_spending_breakdown(session, user_id, start_date, end_date)

    spending_days = len(daily_expenses)
    zero_spending_days = max(0, total_days - spending_days)

    max_spending_day: tuple[date, int] | None = None
    if daily_expenses:
        max_spending_day = max(daily_expenses, key=lambda x: x[1])

    if (
        is_monthly_view
        and start_date.year == today_date.year
        and start_date.month == today_date.month
    ):
        elapsed_days = min(today_date.day, total_days)
        days_for_avg = max(1, elapsed_days)
    else:
        days_for_avg = max(1, total_days)

    avg_daily_expense = exp // days_for_avg
    avg_daily_income = inc // days_for_avg
    avg_expense_per_tx = (exp // exp_cnt) if exp_cnt > 0 else 0

    projected_month_expense: int | None = None
    if (
        is_monthly_view
        and start_date.year == today_date.year
        and start_date.month == today_date.month
    ):
        projected_month_expense = (exp // days_for_avg) * total_days

    category_items = [
        CategoryExpenseItem(
            category_id=cid,
            category_name=cname,
            amount=amt,
            percentage=round((amt / exp) * 100, 1) if exp > 0 else 0.0,
        )
        for cid, cname, amt in raw_breakdown
    ]

    def _to_top_item(raw_t):
        if not raw_t:
            return None
        tid, tdate, cname, amt, note = raw_t
        return TopExpenseItem(
            id=tid,
            date=tdate,
            category_name=cname,
            amount=amt,
            note=note,
        )

    title = f"Statistik Keuangan ({format_display(start_date)} s.d. {format_display(end_date)})"

    return FinancialStatistics(
        title=title,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        spending_days=spending_days,
        zero_spending_days=zero_spending_days,
        total_income=inc,
        total_expense=exp,
        net=inc - exp,
        income_count=inc_cnt,
        expense_count=exp_cnt,
        total_count=inc_cnt + exp_cnt,
        avg_daily_expense=avg_daily_expense,
        avg_daily_income=avg_daily_income,
        avg_expense_per_tx=avg_expense_per_tx,
        max_expense=_to_top_item(extremes["max_expense"]),
        min_expense=_to_top_item(extremes["min_expense"]),
        max_income=_to_top_item(extremes["max_income"]),
        max_spending_day=max_spending_day,
        projected_month_expense=projected_month_expense,
        category_expenses=category_items,
    )


def format_statistics_text(stats: FinancialStatistics) -> str:
    """Format statistik keuangan menjadi teks terminal ASCII."""
    lines: list[str] = []
    lines.append("=" * 65)
    lines.append(f"  {stats.title.upper()}")
    lines.append(
        f"  Rentang: {format_display(stats.start_date)} s.d. {format_display(stats.end_date)} "
        f"({stats.total_days} hari)"
    )
    lines.append("=" * 65)

    lines.append("\n[AKTIVITAS KEUANGAN]")
    lines.append(
        f"  Total Pemasukan       : {format_rupiah(stats.total_income)} "
        f"({stats.income_count} transaksi)"
    )
    lines.append(
        f"  Total Pengeluaran     : {format_rupiah(stats.total_expense)} "
        f"({stats.expense_count} transaksi)"
    )
    net_sign = "+" if stats.net > 0 else ""
    lines.append(f"  Selisih (Net)         : {net_sign}{format_rupiah(stats.net)}")
    lines.append(f"  Total Transaksi Aktif : {stats.total_count} transaksi")
    lines.append(f"  Hari Ada Pengeluaran  : {stats.spending_days} hari")
    lines.append(f"  Hari Bebas Belanja    : {stats.zero_spending_days} hari")

    lines.append("\n[RATA-RATA & INSIGHT]")
    lines.append(f"  Rata-rata Pengeluaran/Hari      : {format_rupiah(stats.avg_daily_expense)}")
    lines.append(f"  Rata-rata Pemasukan/Hari        : {format_rupiah(stats.avg_daily_income)}")
    lines.append(f"  Rata-rata Pengeluaran/Transaksi : {format_rupiah(stats.avg_expense_per_tx)}")
    if stats.projected_month_expense is not None:
        lines.append(
            f"  Proyeksi Pengeluaran Akhir Bulan: {format_rupiah(stats.projected_month_expense)}"
        )

    lines.append("\n[NILAI EKSTREM (TERBESAR & TERKECIL)]")
    if stats.max_expense:
        lines.append(
            f"  Pengeluaran Terbesar : {format_rupiah(stats.max_expense.amount)} "
            f"[{stats.max_expense.category_name}, {format_display(stats.max_expense.date)}]"
        )
    else:
        lines.append("  Pengeluaran Terbesar : -")

    if stats.min_expense:
        lines.append(
            f"  Pengeluaran Terkecil : {format_rupiah(stats.min_expense.amount)} "
            f"[{stats.min_expense.category_name}, {format_display(stats.min_expense.date)}]"
        )
    else:
        lines.append("  Pengeluaran Terkecil : -")

    if stats.max_income:
        lines.append(
            f"  Pemasukan Terbesar   : {format_rupiah(stats.max_income.amount)} "
            f"[{stats.max_income.category_name}, {format_display(stats.max_income.date)}]"
        )
    else:
        lines.append("  Pemasukan Terbesar   : -")

    if stats.max_spending_day:
        d, amt = stats.max_spending_day
        lines.append(f"  Hari Paling Boros    : {format_display(d)} ({format_rupiah(amt)})")
    else:
        lines.append("  Hari Paling Boros    : -")

    lines.append("\n[DISTRIBUSI PENGELUARAN PER KATEGORI]")
    if stats.category_expenses:
        max_len = max(len(c.category_name) for c in stats.category_expenses)
        for c in stats.category_expenses:
            lines.append(
                f"  * {c.category_name.ljust(max_len)} : "
                f"{format_rupiah(c.amount).rjust(14)} ({c.percentage:5.1f}%)"
            )
    else:
        lines.append("  (Tidak ada pengeluaran)")

    lines.append("=" * 65)
    return "\n".join(lines)
