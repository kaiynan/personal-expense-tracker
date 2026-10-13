"""Query agregat database untuk laporan keuangan dan grafik (charts).

Semua kalkulasi dilakukan di level database (SQLAlchemy agregat: SUM, COUNT, GROUP BY),
bukan memuat semua baris transaksi ke memori aplikasi.
Transfer dan adjustment dieksklusikan dari pemasukan/pengeluaran laporan.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from models import (
    STATUS_ACTIVE,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    Category,
    Transaction,
)


def get_period_summary(
    session: Session, user_id: int, start: date, end: date
) -> tuple[int, int, int]:
    """Hitung total pemasukan, total pengeluaran, dan jumlah transaksi aktif dalam periode.

    Hanya menghitung transaksi aktif berjenis 'income' dan 'expense'.
    Transfer dan adjustment tidak masuk.

    Returns:
        (total_pemasukan, total_pengeluaran, jumlah_transaksi)
    """
    t = Transaction
    stmt = select(
        func.coalesce(func.sum(case((t.type == TYPE_INCOME, t.amount), else_=0)), 0),
        func.coalesce(func.sum(case((t.type == TYPE_EXPENSE, t.amount), else_=0)), 0),
        func.count(t.id),
    ).where(
        t.user_id == user_id,
        t.status == STATUS_ACTIVE,
        t.type.in_([TYPE_INCOME, TYPE_EXPENSE]),
        t.date >= start,
        t.date <= end,
    )
    income, expense, count = session.execute(stmt).one()
    return int(income), int(expense), int(count)


def get_category_breakdown(
    session: Session, user_id: int, type_: str, start: date, end: date
) -> list[tuple[int, str, int]]:
    """Pengelompokan nominal per kategori untuk jenis transaksi tertentu ('income' atau 'expense').

    Returns:
        Daftar tuple: `(category_id, category_name, total_nominal)`, terurut dari nominal terbesar.
    """
    t, c = Transaction, Category
    stmt = (
        select(
            c.id,
            c.name,
            func.coalesce(func.sum(t.amount), 0).label("total"),
        )
        .join(c, and_(c.id == t.category_id, c.user_id == t.user_id))
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == type_,
            t.date >= start,
            t.date <= end,
        )
        .group_by(c.id, c.name)
        .order_by(func.sum(t.amount).desc())
    )
    return [(int(cid), str(cname), int(tot)) for cid, cname, tot in session.execute(stmt)]


def get_top_expenses(
    session: Session, user_id: int, start: date, end: date, limit: int = 5
) -> list[tuple[int, date, str, int, str | None]]:
    """Transaksi pengeluaran terbesar dalam periode.

    Returns:
        Daftar tuple: `(id, tanggal, nama_kategori, nominal, keterangan)`.
    """
    t, c = Transaction, Category
    stmt = (
        select(
            t.id,
            t.date,
            func.coalesce(c.name, "-"),
            t.amount,
            t.note,
        )
        .outerjoin(c, and_(c.id == t.category_id, c.user_id == t.user_id))
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == TYPE_EXPENSE,
            t.date >= start,
            t.date <= end,
        )
        .order_by(t.amount.desc(), t.date.desc(), t.id.desc())
        .limit(limit)
    )
    return [
        (int(tid), tdate, str(cname), int(amt), note)
        for tid, tdate, cname, amt, note in session.execute(stmt)
    ]


def get_daily_flows(
    session: Session, user_id: int, start: date, end: date
) -> list[tuple[date, int, int]]:
    """Arus kas pemasukan dan pengeluaran harian dalam periode.

    Returns:
        Daftar tuple: `(tanggal, pemasukan, pengeluaran)` urut tanggal.
    """
    t = Transaction
    stmt = (
        select(
            t.date,
            func.coalesce(func.sum(case((t.type == TYPE_INCOME, t.amount), else_=0)), 0),
            func.coalesce(func.sum(case((t.type == TYPE_EXPENSE, t.amount), else_=0)), 0),
        )
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type.in_([TYPE_INCOME, TYPE_EXPENSE]),
            t.date >= start,
            t.date <= end,
        )
        .group_by(t.date)
        .order_by(t.date.asc())
    )
    return [(d, int(inc), int(exp)) for d, inc, exp in session.execute(stmt)]


def get_daily_balance_deltas(
    session: Session, user_id: int, start: date, end: date
) -> list[tuple[date, int]]:
    """Perubahan saldo total harian: pemasukan (+) - pengeluaran (-) + koreksi (+/-).

    Transfer antar metode milik pengguna sendiri tidak mengubah saldo total.
    Biaya admin dicatat sebagai transaksi pengeluaran terpisah, sehingga sudah
    otomatis tercakup dalam pengurangan pengeluaran.

    Returns:
        Daftar tuple: `(tanggal, delta_saldo)` urut tanggal.
    """
    t = Transaction
    delta_expr = case(
        (t.type == TYPE_INCOME, t.amount),
        (t.type == TYPE_EXPENSE, -t.amount),
        (t.type == TYPE_ADJUSTMENT, t.amount),
        else_=0,
    )
    stmt = (
        select(
            t.date,
            func.coalesce(func.sum(delta_expr), 0),
        )
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type.in_([TYPE_INCOME, TYPE_EXPENSE, TYPE_ADJUSTMENT]),
            t.date >= start,
            t.date <= end,
        )
        .group_by(t.date)
        .order_by(t.date.asc())
    )
    return [(d, int(delta)) for d, delta in session.execute(stmt)]


def get_detailed_period_summary(
    session: Session, user_id: int, start: date, end: date
) -> tuple[int, int, int, int]:
    """Hitung total dan jumlah transaksi terpisah untuk pemasukan dan pengeluaran.

    Returns:
        (total_pemasukan, total_pengeluaran, jumlah_pemasukan, jumlah_pengeluaran)
    """
    t = Transaction
    stmt = select(
        func.coalesce(func.sum(case((t.type == TYPE_INCOME, t.amount), else_=0)), 0),
        func.coalesce(func.sum(case((t.type == TYPE_EXPENSE, t.amount), else_=0)), 0),
        func.coalesce(func.sum(case((t.type == TYPE_INCOME, 1), else_=0)), 0),
        func.coalesce(func.sum(case((t.type == TYPE_EXPENSE, 1), else_=0)), 0),
    ).where(
        t.user_id == user_id,
        t.status == STATUS_ACTIVE,
        t.type.in_([TYPE_INCOME, TYPE_EXPENSE]),
        t.date >= start,
        t.date <= end,
    )
    inc, exp, inc_cnt, exp_cnt = session.execute(stmt).one()
    return int(inc), int(exp), int(inc_cnt), int(exp_cnt)


def get_daily_spending_breakdown(
    session: Session, user_id: int, start: date, end: date
) -> list[tuple[date, int]]:
    """Pengeluaran per hari dalam periode untuk analisis statistik pengeluaran harian.

    Returns:
        Daftar tuple: `(tanggal, total_pengeluaran)` urut tanggal.
    """
    t = Transaction
    stmt = (
        select(
            t.date,
            func.sum(t.amount).label("total_expense"),
        )
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == TYPE_EXPENSE,
            t.date >= start,
            t.date <= end,
        )
        .group_by(t.date)
        .order_by(t.date.asc())
    )
    return [(d, int(exp)) for d, exp in session.execute(stmt)]


def _get_single_transaction_extreme(
    session: Session, user_id: int, type_: str, start: date, end: date, ascending: bool = False
) -> tuple[int, date, str, int, str | None] | None:
    t, c = Transaction, Category
    order_clause = t.amount.asc() if ascending else t.amount.desc()
    date_order = t.date.asc() if ascending else t.date.desc()
    id_order = t.id.asc() if ascending else t.id.desc()
    stmt = (
        select(
            t.id,
            t.date,
            func.coalesce(c.name, "-"),
            t.amount,
            t.note,
        )
        .outerjoin(c, and_(c.id == t.category_id, c.user_id == t.user_id))
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == type_,
            t.date >= start,
            t.date <= end,
        )
        .order_by(order_clause, date_order, id_order)
        .limit(1)
    )
    row = session.execute(stmt).first()
    if not row:
        return None
    tid, tdate, cname, amt, note = row
    return (int(tid), tdate, str(cname), int(amt), note)


def get_extreme_transactions(
    session: Session, user_id: int, start: date, end: date
) -> dict[str, tuple[int, date, str, int, str | None] | None]:
    """Ambil transaksi pengeluaran terbesar, pengeluaran terkecil, dan pemasukan terbesar."""
    return {
        "max_expense": _get_single_transaction_extreme(
            session, user_id, TYPE_EXPENSE, start, end, ascending=False
        ),
        "min_expense": _get_single_transaction_extreme(
            session, user_id, TYPE_EXPENSE, start, end, ascending=True
        ),
        "max_income": _get_single_transaction_extreme(
            session, user_id, TYPE_INCOME, start, end, ascending=False
        ),
    }
