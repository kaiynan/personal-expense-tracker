"""Helper input interaktif untuk CLI dengan validasi berulang dan penanganan pembatalan."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TypeVar

import config
from utils.dates import today
from utils.errors import ValidationError
from utils.validators import parse_date, validate_amount, validate_balance, validate_note

T = TypeVar("T")


class InputCancelled(Exception):
    """Pengguna membatalkan input (mis. mengetik '0', 'batal', atau menekan Ctrl+C)."""


def _raw_prompt(prompt_text: str) -> str:
    """Baca input mentah; tangkap Ctrl+C dan EOF agar tidak crash."""
    try:
        return input(prompt_text)
    except (KeyboardInterrupt, EOFError) as err:
        print()  # baris baru setelah sinyal interupsi
        raise InputCancelled("Input dibatalkan oleh pengguna.") from err


def prompt_str(
    prompt_text: str,
    *,
    max_length: int = config.MAX_NOTE_LENGTH,
    required: bool = False,
    default: str | None = None,
    allow_cancel: bool = True,
) -> str | None:
    """Minta input teks dengan validasi panjang. Kosong mengembalikan default atau None."""
    while True:
        raw = _raw_prompt(prompt_text).strip()
        if not raw:
            if default is not None:
                return default
            if required:
                print("[!] Input tidak boleh kosong. Silakan coba lagi.")
                continue
            return None
        if allow_cancel and raw.lower() == "batal":
            raise InputCancelled("Operasi dibatalkan.")
        try:
            return validate_note(raw)
        except ValidationError as e:
            print(f"[!] {e}")


def prompt_int(
    prompt_text: str,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
    default: int | None = None,
    allow_cancel: bool = True,
    cancel_value: int | None = 0,
) -> int:
    """Minta input bilangan bulat dengan batasan rentang."""
    while True:
        raw = _raw_prompt(prompt_text).strip()
        if not raw and default is not None:
            return default
        if allow_cancel and (
            raw.lower() == "batal" or (cancel_value is not None and raw == str(cancel_value))
        ):
            raise InputCancelled("Operasi dibatalkan.")
        if not raw:
            print("[!] Masukan tidak boleh kosong. Silakan masukkan angka.")
            continue
        try:
            val = int(raw)
        except ValueError:
            print("[!] Nilai harus berupa bilangan bulat tanpa tanda/huruf/desimal.")
            continue
        if min_val is not None and val < min_val:
            print(f"[!] Nilai minimal adalah {min_val:,}.".replace(",", "."))
            continue
        if max_val is not None and val > max_val:
            print(f"[!] Nilai maksimal adalah {max_val:,}.".replace(",", "."))
            continue
        return val


def prompt_amount(
    prompt_text: str,
    *,
    allow_zero: bool = False,
    default: int | None = None,
    allow_cancel: bool = True,
) -> int:
    """Minta nominal uang rupiah (bilangan bulat > 0 atau >= 0 bila allow_zero=True)."""
    while True:
        raw = _raw_prompt(prompt_text).strip()
        if not raw and default is not None:
            return default
        if allow_cancel and (raw.lower() == "batal" or (raw == "0" and not allow_zero)):
            raise InputCancelled("Operasi dibatalkan.")
        if not raw:
            print("[!] Nominal wajib diisi. Silakan coba lagi.")
            continue
        try:
            if allow_zero:
                return validate_balance(raw, "Nominal")
            return validate_amount(raw, "Nominal")
        except ValidationError as e:
            print(f"[!] {e}")


def prompt_date(
    prompt_text: str,
    *,
    default: date | None = None,
    allow_cancel: bool = True,
) -> date:
    """Minta input tanggal format DD-MM-YYYY. Default hari ini bila kosong."""
    effective_default = default if default is not None else today()
    while True:
        raw = _raw_prompt(prompt_text).strip()
        if not raw:
            return effective_default
        if allow_cancel and raw.lower() in ("batal", "0"):
            raise InputCancelled("Operasi dibatalkan.")
        try:
            return parse_date(raw, "Tanggal")
        except ValidationError as e:
            print(f"[!] {e}")


def prompt_choice(
    prompt_text: str,
    choices: list[T],
    display_fn: Callable[[T], str] | None = None,
    *,
    allow_cancel: bool = True,
) -> T:
    """Tampilkan daftar pilihan bernomor lalu minta pengguna memilih satu item."""
    if not choices:
        raise ValueError("Daftar pilihan tidak boleh kosong.")

    for idx, item in enumerate(choices, start=1):
        label = display_fn(item) if display_fn else str(item)
        print(f"  {idx}. {label}")
    if allow_cancel:
        print("  0. Batal")

    while True:
        raw = _raw_prompt(prompt_text).strip()
        if allow_cancel and (raw.lower() == "batal" or raw == "0"):
            raise InputCancelled("Operasi dibatalkan.")
        if not raw:
            print(f"[!] Silakan pilih nomor antara 1 dan {len(choices)}.")
            continue
        try:
            selected_idx = int(raw)
        except ValueError:
            print(f"[!] Masukan harus berupa angka antara 1 dan {len(choices)}.")
            continue
        if 1 <= selected_idx <= len(choices):
            return choices[selected_idx - 1]
        print(f"[!] Pilihan '{raw}' tidak ada di daftar. Silakan pilih 1-{len(choices)}.")


def prompt_confirm(prompt_text: str, default: bool = False) -> bool:
    """Minta konfirmasi Ya/Tidak (y/n)."""
    suffix = " (Y/n): " if default else " (y/N): "
    while True:
        raw = _raw_prompt(prompt_text + suffix).strip().lower()
        if not raw:
            return default
        if raw in ("y", "ya", "yes"):
            return True
        if raw in ("n", "tidak", "no", "batal"):
            return False
        print("[!] Masukan tidak dikenal. Ketik 'y' untuk ya atau 'n' untuk tidak.")
