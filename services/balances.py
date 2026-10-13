"""Service saldo: saldo per metode dan saldo total.

Perhitungan memakai `database.balances` (query agregat), yang menerapkan rumus
tunggal `compute_balance` untuk setiap metode.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from database import balances, queries
from models import TYPE_ADJUSTMENT, TYPE_EXPENSE, TYPE_INCOME, TYPE_TRANSFER
from utils.errors import NotFoundError
from utils.validators import parse_date


@dataclass(frozen=True)
class MethodBalance:
    """Saldo satu metode pembayaran, siap ditampilkan."""

    method_id: int
    name: str
    balance: int
    is_archived: bool


def _as_of(value: object | None):
    return parse_date(value, "Tanggal saldo") if value is not None else None


def get_method_balances(
    session: Session, user_id: int, as_of: object | None = None
) -> list[MethodBalance]:
    """Saldo setiap metode milik `user_id` (termasuk yang diarsipkan, ditandai)."""
    amounts = balances.method_balances(session, user_id, _as_of(as_of))
    return [
        MethodBalance(m.id, m.name, amounts.get(m.id, 0), m.is_archived)
        for m in queries.list_methods(session, user_id)
    ]


def get_method_balance(
    session: Session, user_id: int, method_id: int, as_of: object | None = None
) -> int:
    """Saldo satu metode milik `user_id`; `NotFoundError` bila bukan miliknya."""
    if queries.get_method(session, user_id, method_id) is None:
        raise NotFoundError("Metode pembayaran tidak ditemukan.")
    return balances.method_balances(session, user_id, _as_of(as_of)).get(method_id, 0)


def get_total_balance(session: Session, user_id: int, as_of: object | None = None) -> int:
    """Saldo total `user_id`: jumlah saldo semua metodenya."""
    return balances.total_balance(session, user_id, _as_of(as_of))


@dataclass(frozen=True)
class BalanceConsistencyItem:
    """Hasil cek konsistensi saldo satu metode pembayaran."""

    method_id: int
    name: str
    compute_balance: int
    raw_balance: int
    is_consistent: bool
    difference: int


def check_balance_consistency(session: Session, user_id: int) -> list[BalanceConsistencyItem]:
    """Bandingkan hasil compute_balance dengan perhitungan data transaksi mentah.

    Memverifikasi bahwa query agregat di database.balances menghasilkan nilai yang
    persis sama dengan penjumlahan bertahap dari data awal dan mutasi mentah.
    """
    methods = queries.list_methods(session, user_id)
    agg_balances = balances.method_balances(session, user_id)
    results: list[BalanceConsistencyItem] = []

    for m in methods:
        agg = agg_balances.get(m.id, 0)
        ob = queries.get_opening_balance(session, user_id, m.id)
        effective_date = ob.effective_date if ob else None
        raw = ob.amount if ob else 0

        # Ambil transaksi mentah untuk metode ini
        txs = queries.list_transactions(session, user_id, method_id=m.id, include_deleted=False)
        for t in txs:
            if effective_date is not None and t.date < effective_date:
                continue
            if t.type == TYPE_INCOME and t.method_id == m.id:
                raw += t.amount
            elif t.type == TYPE_EXPENSE and t.method_id == m.id:
                raw -= t.amount
            elif t.type == TYPE_TRANSFER:
                if t.method_id == m.id:
                    raw -= t.amount
                if t.to_method_id == m.id:
                    raw += t.amount
            elif t.type == TYPE_ADJUSTMENT and t.method_id == m.id:
                raw += t.amount

        results.append(
            BalanceConsistencyItem(
                method_id=m.id,
                name=m.name,
                compute_balance=agg,
                raw_balance=raw,
                is_consistent=(agg == raw),
                difference=agg - raw,
            )
        )
    return results
