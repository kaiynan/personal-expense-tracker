"""Validasi input pengguna.

Fungsi murni (nominal, tanggal, keterangan) tidak menyentuh database.
Validasi kategori dan metode membaca database lewat `database.queries`
(selalu dengan `user_id`), bukan SQL langsung.

Setiap fungsi mengembalikan nilai yang sudah dinormalisasi, atau melempar
`ValidationError` dengan pesan yang siap ditampilkan ke pengguna.
"""

import re
from datetime import date, datetime

from sqlalchemy.orm import Session

import config
from database import queries
from models import Category, PaymentMethod
from utils.errors import ValidationError

_DATE_PATTERN = re.compile(r"\d{2}-\d{2}-\d{4}")


def _to_int(value: object, label: str) -> int:
    """Ubah int atau teks angka ("15000") menjadi int. Float dan bool ditolak."""
    # bool adalah subclass int di Python; True tidak boleh dianggap Rp1.
    if isinstance(value, bool):
        raise ValidationError(f"{label} harus berupa bilangan bulat.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return int(text)
        raise ValidationError(f"{label} harus berupa angka bulat tanpa tanda/desimal.")
    # float (termasuk 15000.0), Decimal, None, dan tipe lain ditolak.
    raise ValidationError(f"{label} harus berupa bilangan bulat.")


def _check_max(amount: int, label: str) -> int:
    if amount > config.MAX_AMOUNT:
        raise ValidationError(f"{label} melebihi batas maksimum {config.MAX_AMOUNT:,}.")
    return amount


def validate_amount(value: object, label: str = "Nominal") -> int:
    """Nominal transaksi: bilangan bulat > 0 dan <= `config.MAX_AMOUNT`."""
    amount = _to_int(value, label)
    if amount <= 0:
        raise ValidationError(f"{label} harus lebih dari 0.")
    return _check_max(amount, label)


def validate_balance(value: object, label: str = "Saldo") -> int:
    """Saldo (saldo awal / saldo sebenarnya): bilangan bulat >= 0 dan <= batas."""
    amount = _to_int(value, label)
    if amount < 0:
        raise ValidationError(f"{label} tidak boleh negatif.")
    return _check_max(amount, label)


def parse_date(value: object, label: str = "Tanggal") -> date:
    """Tanggal dari teks `DD-MM-YYYY` (dua digit hari/bulan) atau objek `date`.

    `datetime` ditolak agar tidak ada kebingungan zona waktu.
    """
    if isinstance(value, datetime):
        raise ValidationError(f"{label} harus berupa tanggal, bukan tanggal-waktu.")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _DATE_PATTERN.fullmatch(text):
            try:
                return datetime.strptime(text, config.DATE_FORMAT_DISPLAY).date()
            except ValueError:
                pass  # mis. 31-02-2026
        raise ValidationError(f"{label} tidak valid. Gunakan format DD-MM-YYYY.")
    raise ValidationError(f"{label} tidak valid. Gunakan format DD-MM-YYYY.")


def validate_note(value: object) -> str | None:
    """Keterangan opsional: dipangkas spasinya; kosong menjadi `None`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Keterangan harus berupa teks.")
    text = value.strip()
    if not text:
        return None
    if len(text) > config.MAX_NOTE_LENGTH:
        raise ValidationError(
            f"Keterangan maksimal {config.MAX_NOTE_LENGTH} karakter (sekarang {len(text)})."
        )
    return text


def validate_method(session: Session, user_id: int, method_id: object) -> PaymentMethod:
    """Metode harus ada, milik `user_id`, dan tidak diarsipkan."""
    if method_id is None:
        raise ValidationError("Metode pembayaran wajib dipilih.")
    if isinstance(method_id, bool) or not isinstance(method_id, int):
        raise ValidationError("Metode pembayaran tidak valid.")
    method = queries.get_method(session, user_id, method_id)
    if method is None:
        raise ValidationError("Metode pembayaran tidak ditemukan.")
    if method.is_archived:
        raise ValidationError(f"Metode pembayaran '{method.name}' sudah diarsipkan.")
    return method


def validate_category(
    session: Session,
    user_id: int,
    category_id: object,
    expected_type: str,
    allow_system: bool = False,
) -> Category:
    """Kategori harus ada, milik `user_id`, tidak diarsipkan, dan jenisnya cocok.

    Kategori sistem (mis. "Biaya Admin") hanya dipakai otomatis oleh service,
    sehingga ditolak untuk input manual kecuali `allow_system=True`.
    """
    if category_id is None:
        raise ValidationError("Kategori wajib dipilih.")
    if isinstance(category_id, bool) or not isinstance(category_id, int):
        raise ValidationError("Kategori tidak valid.")
    category = queries.get_category(session, user_id, category_id)
    if category is None:
        raise ValidationError("Kategori tidak ditemukan.")
    if category.is_archived:
        raise ValidationError(f"Kategori '{category.name}' sudah diarsipkan.")
    if category.type != expected_type:
        raise ValidationError(f"Kategori '{category.name}' bukan kategori {expected_type}.")
    if category.is_system and not allow_system:
        raise ValidationError(f"Kategori '{category.name}' dipakai otomatis oleh sistem.")
    return category
