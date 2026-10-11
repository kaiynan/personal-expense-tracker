"""Service saldo: saldo per metode dan saldo total.

Perhitungan memakai `database.balances` (query agregat), yang menerapkan rumus
tunggal `compute_balance` untuk setiap metode.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from database import balances, queries
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
