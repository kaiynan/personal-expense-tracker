"""Helper bersama untuk service: cek pengguna, tanggal default, dan peringatan.

Cara kerja peringatan saldo minus: ambil "foto" saldo semua metode sebelum
perubahan, lakukan perubahan, `flush`, lalu bandingkan. Metode diberi peringatan
bila saldonya sesudah perubahan < 0 **dan** lebih rendah dari sebelumnya
(operasi ini yang membuat/memperdalam minus). Saldo yang dipakai adalah saldo
keseluruhan saat ini (semua transaksi aktif, tanpa batas `as_of`).
"""

from collections.abc import Iterable
from datetime import date

from sqlalchemy.orm import Session

from database import queries
from database.balances import method_balances
from models import User
from services.results import BEFORE_OPENING_DATE, NEGATIVE_BALANCE, ServiceWarning
from utils.dates import format_display, today
from utils.errors import NotFoundError
from utils.formatting import format_rupiah
from utils.validators import parse_date


def require_user(session: Session, user_id: int) -> User:
    """Pastikan pengguna ada."""
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    return user


def resolve_date(user: User, value: object | None) -> date:
    """Tanggal dari input pengguna; `None` berarti hari ini di zona waktu pengguna."""
    if value is None:
        return today(user.timezone)
    return parse_date(value)


def _unique_ids(method_ids: Iterable[int | None]) -> list[int]:
    return list(dict.fromkeys(m for m in method_ids if m is not None))


def balance_snapshot(session: Session, user_id: int) -> dict[int, int]:
    """Saldo semua metode saat ini, termasuk perubahan yang belum di-commit."""
    session.flush()
    return method_balances(session, user_id)


def negative_balance_warnings(
    session: Session,
    user_id: int,
    before: dict[int, int],
    method_ids: Iterable[int | None],
) -> list[ServiceWarning]:
    """Peringatan untuk metode yang saldonya menjadi (lebih) minus setelah perubahan."""
    after = balance_snapshot(session, user_id)
    warnings: list[ServiceWarning] = []
    for method_id in _unique_ids(method_ids):
        new, old = after.get(method_id, 0), before.get(method_id, 0)
        if new < 0 and new < old:
            method = queries.get_method(session, user_id, method_id)
            name = method.name if method else f"#{method_id}"
            warnings.append(
                ServiceWarning(
                    NEGATIVE_BALANCE,
                    f"Saldo {name} menjadi minus: {format_rupiah(new)}.",
                    method_id,
                )
            )
    return warnings


def before_opening_warnings(
    session: Session,
    user_id: int,
    tx_date: date,
    method_ids: Iterable[int | None],
) -> list[ServiceWarning]:
    """Peringatan bila tanggal transaksi sebelum tanggal berlaku saldo awal metode.

    Transaksi seperti itu tetap tersimpan, tetapi tidak memengaruhi saldo karena
    dianggap sudah termasuk dalam saldo awal.
    """
    warnings: list[ServiceWarning] = []
    for method_id in _unique_ids(method_ids):
        opening = queries.get_opening_balance(session, user_id, method_id)
        if opening is not None and tx_date < opening.effective_date:
            method = queries.get_method(session, user_id, method_id)
            name = method.name if method else f"#{method_id}"
            warnings.append(
                ServiceWarning(
                    BEFORE_OPENING_DATE,
                    f"Tanggal {format_display(tx_date)} sebelum tanggal berlaku saldo awal "
                    f"{name} ({format_display(opening.effective_date)}); transaksi ini "
                    "tidak memengaruhi saldo.",
                    method_id,
                )
            )
    return warnings
