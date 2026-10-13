"""Service transaksi rutin: kelola jadwal dan penerapan transaksi rutin (kos, internet, langganan).

Semua fungsi sinkron, menerima `Session` dan `user_id`, dan tidak melakukan commit
agar pemanggil dapat mengaturnya di dalam `session_scope`.
"""

import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from database import queries
from models import (
    FREQUENCY_DAILY,
    FREQUENCY_MONTHLY,
    FREQUENCY_WEEKLY,
    FREQUENCY_YEARLY,
    TYPE_EXPENSE,
    TYPE_INCOME,
    RecurringTransaction,
)
from services.common import require_user, resolve_date
from services.results import TransactionResult
from services.transactions import add_expense, add_income
from utils.errors import NotFoundError, ValidationError
from utils.validators import (
    parse_date,
    validate_amount,
    validate_category,
    validate_category_type,
    validate_day_of_month,
    validate_frequency,
    validate_method,
    validate_note,
    validate_recurring_name,
)

# Marker untuk parameter yang tidak diubah pada update
_UNSET = object()


def _calculate_next_due_date(
    recurring: RecurringTransaction, from_date: date | None = None
) -> date | None:
    """Hitung tanggal jatuh tempo berikutnya untuk suatu transaksi rutin.

    Mengembalikan None jika transaksi rutin tidak aktif atau sudah melewati `end_date`.
    """
    if not recurring.is_active:
        return None

    # Tentukan tanggal acuan
    if from_date is not None:
        ref_date = from_date
    elif recurring.last_generated_date is not None:
        ref_date = recurring.last_generated_date
    else:
        ref_date = None

    if ref_date is None:
        # Belum pernah dicatat, mulai dari start_date
        due: date
        if recurring.frequency in (FREQUENCY_DAILY, FREQUENCY_WEEKLY, FREQUENCY_YEARLY):
            due = recurring.start_date
        elif recurring.frequency == FREQUENCY_MONTHLY:
            target_day = recurring.day_of_month or recurring.start_date.day
            max_day = calendar.monthrange(recurring.start_date.year, recurring.start_date.month)[1]
            cand = date(
                recurring.start_date.year, recurring.start_date.month, min(target_day, max_day)
            )
            if cand >= recurring.start_date:
                due = cand
            else:
                y = recurring.start_date.year
                m = recurring.start_date.month + 1
                if m > 12:
                    y += 1
                    m = 1
                due = date(y, m, min(target_day, calendar.monthrange(y, m)[1]))
        else:
            due = recurring.start_date
    else:
        # Sudah ada tanggal acuan / pencatatan terakhir, hitung periode berikutnya
        if recurring.frequency == FREQUENCY_DAILY:
            due = ref_date + timedelta(days=1)
        elif recurring.frequency == FREQUENCY_WEEKLY:
            due = ref_date + timedelta(days=7)
        elif recurring.frequency == FREQUENCY_MONTHLY:
            target_day = recurring.day_of_month or recurring.start_date.day
            y = ref_date.year
            m = ref_date.month + 1
            if m > 12:
                y += 1
                m = 1
            due = date(y, m, min(target_day, calendar.monthrange(y, m)[1]))
        elif recurring.frequency == FREQUENCY_YEARLY:
            y = ref_date.year + 1
            m = recurring.start_date.month
            due = date(y, m, min(recurring.start_date.day, calendar.monthrange(y, m)[1]))
        else:
            due = ref_date + timedelta(days=30)

    # Periksa batas akhir jika ada
    if recurring.end_date is not None and due > recurring.end_date:
        return None

    return due


def _is_recurring_due(recurring: RecurringTransaction, as_of: date) -> bool:
    """Periksa apakah transaksi rutin jatuh tempo per tanggal `as_of`."""
    if not recurring.is_active:
        return False
    nxt = _calculate_next_due_date(recurring)
    if nxt is None:
        return False
    return nxt <= as_of


def get_next_due_date(session: Session, user_id: int, recurring_id: int) -> date | None:
    """Ambil tanggal jatuh tempo berikutnya untuk transaksi rutin milik user_id."""
    require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")
    return _calculate_next_due_date(recurring)


def create_recurring(
    session: Session,
    user_id: int,
    name: str,
    type_: str,
    amount: object,
    category_id: int,
    method_id: int,
    frequency: str = FREQUENCY_MONTHLY,
    day_of_month: int | None = None,
    start_date: object | None = None,
    end_date: object | None = None,
    note: object | None = None,
) -> RecurringTransaction:
    """Buat aturan transaksi rutin baru milik `user_id`."""
    user = require_user(session, user_id)
    clean_name = validate_recurring_name(name)
    clean_type = validate_category_type(type_)
    clean_amount = validate_amount(amount)
    category = validate_category(session, user_id, category_id, clean_type)
    method = validate_method(session, user_id, method_id)
    clean_freq = validate_frequency(frequency)
    clean_day = validate_day_of_month(day_of_month, clean_freq)
    clean_start = resolve_date(user, start_date)
    clean_end = parse_date(end_date, "Tanggal berakhir") if end_date is not None else None
    clean_note = validate_note(note)

    if clean_end is not None and clean_end < clean_start:
        raise ValidationError("Tanggal berakhir tidak boleh mendahului tanggal mulai.")

    recurring = RecurringTransaction(
        user_id=user_id,
        name=clean_name,
        type=clean_type,
        amount=clean_amount,
        category_id=category.id,
        method_id=method.id,
        frequency=clean_freq,
        day_of_month=clean_day,
        start_date=clean_start,
        end_date=clean_end,
        note=clean_note,
        is_active=True,
    )
    session.add(recurring)
    session.flush()
    return recurring


def get_recurring(session: Session, user_id: int, recurring_id: int) -> RecurringTransaction | None:
    """Ambil satu transaksi rutin milik `user_id`."""
    return queries.get_recurring(session, user_id, recurring_id)


def list_recurring(
    session: Session,
    user_id: int,
    *,
    is_active: bool | None = None,
) -> list[RecurringTransaction]:
    """Daftar transaksi rutin milik `user_id`."""
    return queries.list_recurring(session, user_id, is_active=is_active)


def update_recurring(
    session: Session,
    user_id: int,
    recurring_id: int,
    *,
    name: object = _UNSET,
    amount: object = _UNSET,
    category_id: object = _UNSET,
    method_id: object = _UNSET,
    frequency: object = _UNSET,
    day_of_month: object = _UNSET,
    start_date: object = _UNSET,
    end_date: object = _UNSET,
    note: object = _UNSET,
    is_active: object = _UNSET,
) -> RecurringTransaction:
    """Perbarui aturan transaksi rutin."""
    require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")

    if name is not _UNSET:
        recurring.name = validate_recurring_name(name)
    if amount is not _UNSET:
        recurring.amount = validate_amount(amount)
    if category_id is not _UNSET:
        cat = validate_category(session, user_id, category_id, recurring.type)
        recurring.category_id = cat.id
    if method_id is not _UNSET:
        met = validate_method(session, user_id, method_id)
        recurring.method_id = met.id
    if frequency is not _UNSET:
        recurring.frequency = validate_frequency(frequency)
    if day_of_month is not _UNSET:
        recurring.day_of_month = validate_day_of_month(day_of_month, recurring.frequency)
    if start_date is not _UNSET:
        recurring.start_date = parse_date(start_date, "Tanggal mulai")
    if end_date is not _UNSET:
        recurring.end_date = parse_date(end_date, "Tanggal berakhir") if end_date else None
    if note is not _UNSET:
        recurring.note = validate_note(note)
    if is_active is not _UNSET:
        recurring.is_active = bool(is_active)

    if (
        recurring.end_date is not None
        and recurring.start_date is not None
        and recurring.end_date < recurring.start_date
    ):
        raise ValidationError("Tanggal berakhir tidak boleh mendahului tanggal mulai.")

    session.flush()
    return recurring


def activate_recurring(session: Session, user_id: int, recurring_id: int) -> RecurringTransaction:
    """Aktifkan kembali transaksi rutin yang nonaktif."""
    require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")
    if recurring.is_active:
        raise ValidationError("Transaksi rutin sudah dalam keadaan aktif.")
    recurring.is_active = True
    session.flush()
    return recurring


def deactivate_recurring(session: Session, user_id: int, recurring_id: int) -> RecurringTransaction:
    """Nonaktifkan transaksi rutin tanpa menghapusnya."""
    require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")
    if not recurring.is_active:
        raise ValidationError("Transaksi rutin sudah dalam keadaan nonaktif.")
    recurring.is_active = False
    session.flush()
    return recurring


def delete_recurring(session: Session, user_id: int, recurring_id: int) -> None:
    """Hapus aturan transaksi rutin."""
    require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")
    session.delete(recurring)
    session.flush()


def apply_recurring(
    session: Session,
    user_id: int,
    recurring_id: int,
    on: object | None = None,
) -> TransactionResult:
    """Catat transaksi nyata dari template transaksi rutin.

    Mencatat transaksi pemasukan/pengeluaran dan memperbarui `last_generated_date`.
    """
    user = require_user(session, user_id)
    recurring = queries.get_recurring(session, user_id, recurring_id)
    if recurring is None:
        raise NotFoundError("Transaksi rutin tidak ditemukan.")

    tx_date = resolve_date(user, on)

    # Susun keterangan transaksi
    if recurring.note:
        tx_note = f"[Rutin] {recurring.name} - {recurring.note}"[:255]
    else:
        tx_note = f"[Rutin] {recurring.name}"[:255]

    if recurring.type == TYPE_EXPENSE:
        result = add_expense(
            session,
            user_id,
            amount=recurring.amount,
            category_id=recurring.category_id,
            method_id=recurring.method_id,
            on=tx_date,
            note=tx_note,
        )
    elif recurring.type == TYPE_INCOME:
        result = add_income(
            session,
            user_id,
            amount=recurring.amount,
            category_id=recurring.category_id,
            method_id=recurring.method_id,
            on=tx_date,
            note=tx_note,
        )
    else:
        raise ValidationError(f"Jenis transaksi rutin tidak valid: {recurring.type}")

    recurring.last_generated_date = result.transaction.date
    session.flush()
    return result


def process_due_recurring(
    session: Session,
    user_id: int,
    as_of: object | None = None,
) -> list[TransactionResult]:
    """Proses semua transaksi rutin aktif yang sudah jatuh tempo per tanggal `as_of`."""
    user = require_user(session, user_id)
    as_of_date = resolve_date(user, as_of)

    active_items = queries.list_recurring(session, user_id, is_active=True)
    results: list[TransactionResult] = []

    for item in active_items:
        # Proses jika ada periode belum tercatat sampai as_of_date (maks 24 iterasi)
        loop_guard = 0
        while _is_recurring_due(item, as_of_date) and loop_guard < 24:
            due_date = _calculate_next_due_date(item)
            if due_date is None or due_date > as_of_date:
                break
            res = apply_recurring(session, user_id, item.id, on=due_date)
            results.append(res)
            loop_guard += 1

    return results
