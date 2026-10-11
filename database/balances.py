"""Perhitungan saldo sebagai query agregat eksplisit.

Saldo TIDAK disimpan; selalu dihitung dari data:

    saldo_metode = saldo_awal + pemasukan - pengeluaran
                   + transfer_masuk - transfer_keluar + koreksi

    saldo_total  = jumlah semua saldo_metode

Aturan yang berlaku di semua query di sini:
- hanya transaksi berstatus `active` (soft delete diabaikan);
- hanya transaksi milik `user_id` yang diminta;
- hanya transaksi bertanggal >= `effective_date` saldo awal metode tersebut
  (transaksi sebelumnya dianggap sudah termasuk dalam saldo awal). Bila metode
  belum punya saldo awal, semua transaksinya dihitung dari nol;
- `as_of` (opsional) membatasi sampai tanggal tertentu, termasuk untuk saldo
  awal: saldo awal yang berlaku setelah `as_of` belum dihitung;
- semua hasil bertipe `int` (rupiah), tidak pernah float.

Transfer dan adjustment ikut menghitung saldo, tetapi BUKAN pemasukan/pengeluaran
di laporan; itu dibahas di modul laporan, bukan di sini.
"""

from datetime import date
from typing import NamedTuple

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from models import (
    STATUS_ACTIVE,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_TRANSFER,
    OpeningBalance,
    PaymentMethod,
    Transaction,
)


class MethodFlows(NamedTuple):
    """Total arus keluar-masuk satu metode (rupiah, integer)."""

    income: int = 0
    expense: int = 0
    transfer_out: int = 0
    adjustment: int = 0


def compute_balance(
    opening: int,
    income: int,
    expense: int,
    transfer_in: int,
    transfer_out: int,
    adjustment: int,
) -> int:
    """Rumus saldo satu metode. Fungsi murni: satu-satunya tempat rumus ini ditulis."""
    return opening + income - expense + transfer_in - transfer_out + adjustment


def _openings(session: Session, user_id: int, as_of: date | None) -> dict[int, int]:
    """Saldo awal per metode yang sudah berlaku pada `as_of`."""
    stmt = select(OpeningBalance.method_id, OpeningBalance.amount).where(
        OpeningBalance.user_id == user_id
    )
    if as_of is not None:
        stmt = stmt.where(OpeningBalance.effective_date <= as_of)
    return {method_id: int(amount) for method_id, amount in session.execute(stmt)}


def _outgoing_flows(session: Session, user_id: int, as_of: date | None) -> dict[int, MethodFlows]:
    """Total pemasukan, pengeluaran, transfer keluar, dan koreksi per metode asal."""
    t, ob = Transaction, OpeningBalance

    def total(kind: str):
        return func.coalesce(func.sum(case((t.type == kind, t.amount), else_=0)), 0)

    stmt = (
        select(
            t.method_id,
            total(TYPE_INCOME),
            total(TYPE_EXPENSE),
            total(TYPE_TRANSFER),
            total(TYPE_ADJUSTMENT),
        )
        .outerjoin(ob, and_(ob.user_id == t.user_id, ob.method_id == t.method_id))
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            or_(ob.effective_date.is_(None), t.date >= ob.effective_date),
        )
        .group_by(t.method_id)
    )
    if as_of is not None:
        stmt = stmt.where(t.date <= as_of)

    return {
        method_id: MethodFlows(int(income), int(expense), int(transfer_out), int(adjustment))
        for method_id, income, expense, transfer_out, adjustment in session.execute(stmt)
    }


def _incoming_transfers(session: Session, user_id: int, as_of: date | None) -> dict[int, int]:
    """Total transfer masuk per metode tujuan."""
    t, ob = Transaction, OpeningBalance

    stmt = (
        select(t.to_method_id, func.coalesce(func.sum(t.amount), 0))
        .outerjoin(ob, and_(ob.user_id == t.user_id, ob.method_id == t.to_method_id))
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == TYPE_TRANSFER,
            or_(ob.effective_date.is_(None), t.date >= ob.effective_date),
        )
        .group_by(t.to_method_id)
    )
    if as_of is not None:
        stmt = stmt.where(t.date <= as_of)

    return {method_id: int(total) for method_id, total in session.execute(stmt)}


def method_balances(session: Session, user_id: int, as_of: date | None = None) -> dict[int, int]:
    """Saldo setiap metode pembayaran milik `user_id` (termasuk yang diarsipkan).

    Args:
        as_of: bila diisi, hitung saldo sampai tanggal ini (inklusif).

    Returns:
        Peta `method_id -> saldo` dalam rupiah (boleh negatif).
    """
    method_ids = session.scalars(
        select(PaymentMethod.id).where(PaymentMethod.user_id == user_id)
    ).all()
    openings = _openings(session, user_id, as_of)
    outgoing = _outgoing_flows(session, user_id, as_of)
    incoming = _incoming_transfers(session, user_id, as_of)
    no_flows = MethodFlows()

    balances: dict[int, int] = {}
    for method_id in method_ids:
        flows = outgoing.get(method_id, no_flows)
        balances[method_id] = compute_balance(
            opening=openings.get(method_id, 0),
            income=flows.income,
            expense=flows.expense,
            transfer_in=incoming.get(method_id, 0),
            transfer_out=flows.transfer_out,
            adjustment=flows.adjustment,
        )
    return balances


def total_balance(session: Session, user_id: int, as_of: date | None = None) -> int:
    """Saldo total: jumlah saldo semua metode pembayaran `user_id`."""
    return sum(method_balances(session, user_id, as_of).values())
