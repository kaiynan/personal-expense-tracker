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

import config
from database import queries
from database.balances import method_balances
from models import ROLE_ADMIN, USER_STATUS_ACTIVE, User
from services.results import (
    BEFORE_OPENING_DATE,
    NEGATIVE_BALANCE,
    NEWER_ADJUSTMENT,
    ServiceWarning,
)
from utils.dates import format_display, today
from utils.errors import NotFoundError, ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date


def require_user(session: Session, user_id: int) -> User:
    """Pastikan pengguna ada dan berstatus aktif."""
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    if user.status != USER_STATUS_ACTIVE:
        raise ValidationError(f"Akun pengguna belum aktif atau diblokir (status: {user.status}).")
    return user


def require_admin(session: Session, user_id: int) -> User:
    """Pastikan pengguna ada, aktif, dan memiliki peran admin."""
    user = require_user(session, user_id)
    if user.role != ROLE_ADMIN:
        raise ValidationError("Hanya admin yang dapat menjalankan perintah ini.")
    return user


def check_daily_transaction_limit(
    session: Session, user_id: int, tx_date: date, count: int = 1
) -> None:
    """Pastikan batas jumlah transaksi harian per pengguna belum terlampaui."""
    limit = config.MAX_DAILY_TRANSACTIONS_PER_USER
    if limit <= 0:
        return
    current_count = queries.count_transactions_for_user_on_date(session, user_id, tx_date)
    if current_count + count > limit:
        raise ValidationError(
            f"Batas transaksi harian ({limit}) untuk tanggal "
            f"{format_display(tx_date)} telah tercapai."
        )


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


def newer_adjustment_warnings(
    session: Session,
    user_id: int,
    tx_date: date,
    method_ids: Iterable[int | None],
    exclude_id: int | None = None,
) -> list[ServiceWarning]:
    """Peringatan bila transaksi diedit/dihapus padahal ada koreksi saldo yang lebih baru.

    Koreksi tersebut mungkin perlu diperbarui karena perhitungannya didasarkan
    pada saldo transaksi sebelum perubahan.
    """
    warnings: list[ServiceWarning] = []
    for method_id in _unique_ids(method_ids):
        newer = queries.get_newer_adjustments(
            session, user_id, method_id, after_date=tx_date, exclude_id=exclude_id
        )
        if newer:
            method = queries.get_method(session, user_id, method_id)
            name = method.name if method else f"#{method_id}"
            adj_dates = ", ".join(format_display(a.date) for a in newer[:2])
            warnings.append(
                ServiceWarning(
                    NEWER_ADJUSTMENT,
                    f"Terdapat koreksi saldo pada {name} bertanggal {adj_dates}; "
                    "koreksi tersebut mungkin perlu diperbarui.",
                    method_id,
                )
            )
    return warnings
