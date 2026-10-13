"""Layanan pengelolaan alias input cepat dan parsing teks transaksi (V2).

Memungkinkan pencatatan transaksi cepat di bot Telegram (mis. `makan 15000 gopay`)
dengan tabel alias yang dapat dipersonalisasi pengguna.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from database.queries import (
    get_category,
    get_method,
    get_quick_alias,
    get_quick_alias_by_name,
    list_categories,
    list_methods,
    list_quick_aliases,
)
from database.seed import seed_default_aliases as _db_seed_aliases
from models import (
    ACTION_CREATE,
    ACTION_DELETE,
    ALIAS_TARGET_TYPES,
    TARGET_CATEGORY,
    TARGET_PAYMENT_METHOD,
    QuickAlias,
)
from services.audit import record_audit_log
from services.common import require_user
from utils.errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class ParsedQuickTransaction:
    """Hasil ekstraksi teks input cepat transaksi."""

    type: str  # "expense" atau "income"
    amount: int  # integer rupiah > 0
    category_id: int
    category_name: str
    method_id: int
    method_name: str
    note: str | None
    raw_text: str


def add_alias(
    session: Session,
    user_id: int,
    alias: str,
    target_type: str,
    target_id: int,
) -> QuickAlias:
    """Daftarkan alias input cepat baru untuk kategori atau metode pembayaran."""
    require_user(session, user_id)

    alias_clean = alias.strip().lower()
    if not alias_clean:
        raise ValidationError("Teks alias tidak boleh kosong")

    if target_type not in ALIAS_TARGET_TYPES:
        raise ValidationError(
            f"Tipe target '{target_type}' tidak valid. Pilih 'category' atau 'payment_method'."
        )

    # Cek duplikasi
    existing = get_quick_alias_by_name(session, user_id, alias_clean)
    if existing is not None:
        raise ValidationError(f"Alias '{alias_clean}' sudah digunakan")

    cat_id = None
    meth_id = None
    target_name = ""

    if target_type == TARGET_CATEGORY:
        cat = get_category(session, user_id, target_id)
        if cat is None or cat.is_archived:
            raise NotFoundError(f"Kategori #{target_id} tidak ditemukan atau telah diarsipkan")
        cat_id = cat.id
        target_name = cat.name
    else:
        meth = get_method(session, user_id, target_id)
        if meth is None or meth.is_archived:
            raise NotFoundError(
                f"Metode pembayaran #{target_id} tidak ditemukan atau telah diarsipkan"
            )
        meth_id = meth.id
        target_name = meth.name

    qa = QuickAlias(
        user_id=user_id,
        alias=alias_clean,
        target_type=target_type,
        category_id=cat_id,
        method_id=meth_id,
    )
    session.add(qa)
    session.flush()

    record_audit_log(
        session,
        user_id,
        ACTION_CREATE,
        "quick_alias",
        qa.id,
        new_values={
            "alias": qa.alias,
            "target_type": qa.target_type,
            "target_id": target_id,
            "target_name": target_name,
        },
        description=f"Tambah alias '{qa.alias}' ➔ {target_name}",
    )
    return qa


def delete_alias(session: Session, user_id: int, alias_id: int) -> bool:
    """Hapus alias input cepat milik pengguna."""
    require_user(session, user_id)
    qa = get_quick_alias(session, user_id, alias_id)
    if qa is None:
        raise NotFoundError(f"Alias #{alias_id} tidak ditemukan")

    target_id = qa.category_id if qa.target_type == TARGET_CATEGORY else qa.method_id
    record_audit_log(
        session,
        user_id,
        ACTION_DELETE,
        "quick_alias",
        qa.id,
        old_values={
            "alias": qa.alias,
            "target_type": qa.target_type,
            "target_id": target_id,
        },
        description=f"Hapus alias '{qa.alias}'",
    )
    session.delete(qa)
    session.flush()
    return True


def list_aliases(
    session: Session, user_id: int, target_type: str | None = None
) -> list[QuickAlias]:
    """Daftar seluruh alias input cepat milik pengguna."""
    require_user(session, user_id)
    return list_quick_aliases(session, user_id, target_type)


def get_alias(session: Session, user_id: int, alias_id: int) -> QuickAlias:
    """Ambil data alias input cepat berdasarkan ID."""
    require_user(session, user_id)
    qa = get_quick_alias(session, user_id, alias_id)
    if qa is None:
        raise NotFoundError(f"Alias #{alias_id} tidak ditemukan")
    return qa


def seed_default_aliases(session: Session, user_id: int) -> int:
    """Isi alias bawaan untuk pengguna jika belum tersedia."""
    require_user(session, user_id)
    return _db_seed_aliases(session, user_id)


def _parse_amount_token(token: str) -> int | None:
    """Ekstrak nominal dari token teks.

    Mendukung format:
    - 15000, 15.000, 15,000
    - 15k, 15rb, 15ribu
    - 1.5jt, 2jt, 1.5juta
    - rp15000, rp15k
    """
    cleaned = token.strip().lower()
    if cleaned.startswith("rp"):
        cleaned = cleaned[2:].lstrip(".").strip()

    if not cleaned:
        return None

    # Format juta: mis. "1.5jt", "2jt", "1,5juta"
    match_jt = re.match(r"^(\d+(?:[.,]\d+)?)\s*(?:jt|juta)$", cleaned)
    if match_jt:
        num_str = match_jt.group(1).replace(",", ".")
        try:
            return int(float(num_str) * 1_000_000)
        except ValueError:
            return None

    # Format ribu: mis. "15k", "15rb", "15ribu"
    match_rb = re.match(r"^(\d+(?:[.,]\d+)?)\s*(?:k|rb|ribu)$", cleaned)
    if match_rb:
        num_str = match_rb.group(1).replace(",", ".")
        try:
            return int(float(num_str) * 1_000)
        except ValueError:
            return None

    # Format angka murni (dengan pemisah titik/koma)
    plain = cleaned.replace(".", "").replace(",", "")
    if plain.isdigit():
        return int(plain)

    return None


def parse_quick_input(session: Session, user_id: int, text: str) -> ParsedQuickTransaction:
    """Urai kalimat input cepat menjadi transaksi lengkap dengan resolusi alias.

    Contoh:
    - `makan 15000 gopay` ➔ Makanan, Rp15.000, E-Wallet
    - `bensin 25k cash bensin motor` ➔ Transportasi, Rp25.000, Cash, Note: 'bensin motor'
    - `gaji 5jt bank` ➔ Gaji, Rp5.000.000, Bank
    """
    require_user(session, user_id)

    raw_text = text.strip()
    if not raw_text:
        raise ValidationError("Teks input cepat tidak boleh kosong")

    tokens = raw_text.split()

    # 1. Cari nominal transaksi
    amount: int | None = None
    amount_idx: int | None = None
    for idx, tok in enumerate(tokens):
        amt = _parse_amount_token(tok)
        if amt is not None:
            if amt <= 0:
                raise ValidationError("Nominal harus lebih dari 0")
            amount = amt
            amount_idx = idx
            break

    if amount is None or amount_idx is None:
        raise ValidationError(
            "Nominal transaksi tidak ditemukan. Masukkan angka (mis. '15000', '15k', '1.5jt')."
        )

    remaining_tokens = [tok for i, tok in enumerate(tokens) if i != amount_idx]
    if not remaining_tokens:
        raise ValidationError("Kategori atau keterangan transaksi tidak ditemukan.")

    # 2. Ambil seluruh alias, kategori, dan metode milik pengguna
    user_aliases = list_quick_aliases(session, user_id)
    alias_map = {a.alias.lower(): a for a in user_aliases}

    active_cats = [c for c in list_categories(session, user_id) if not c.is_archived]
    cat_by_id = {c.id: c for c in active_cats}
    cat_by_name = {c.name.lower(): c for c in active_cats}

    active_methods = [m for m in list_methods(session, user_id) if not m.is_archived]
    meth_by_id = {m.id: m for m in active_methods}
    meth_by_name = {m.name.lower(): m for m in active_methods}

    matched_cat_id: int | None = None
    matched_meth_id: int | None = None
    used_token_indices: set[int] = set()

    # Cek kombinasi 2 kata terlebih dahulu, lalu 1 kata
    n = len(remaining_tokens)
    for length in (2, 1):
        for start in range(n - length + 1):
            indices = set(range(start, start + length))
            if indices & used_token_indices:
                continue

            phrase = " ".join(remaining_tokens[start : start + length]).lower()

            # A. Cek di tabel alias
            if phrase in alias_map:
                qa = alias_map[phrase]
                if (
                    qa.target_type == TARGET_CATEGORY
                    and matched_cat_id is None
                    and qa.category_id in cat_by_id
                ):
                    matched_cat_id = qa.category_id
                    used_token_indices |= indices
                elif (
                    qa.target_type == TARGET_PAYMENT_METHOD
                    and matched_meth_id is None
                    and qa.method_id in meth_by_id
                ):
                    matched_meth_id = qa.method_id
                    used_token_indices |= indices
                continue

            # B. Cek nama kategori langsung
            if matched_cat_id is None and phrase in cat_by_name:
                matched_cat_id = cat_by_name[phrase].id
                used_token_indices |= indices
                continue

            # C. Cek nama metode langsung
            if matched_meth_id is None and phrase in meth_by_name:
                matched_meth_id = meth_by_name[phrase].id
                used_token_indices |= indices
                continue

    if matched_cat_id is None:
        raise ValidationError(
            "Kategori tidak dikenali. Gunakan nama kategori atau daftarkan alias terlebih dahulu."
        )

    cat_obj = cat_by_id[matched_cat_id]

    # Jika metode belum ditentukan, pilih metode pertama (default: Cash)
    if matched_meth_id is None:
        if not active_methods:
            raise ValidationError("Tidak ada metode pembayaran aktif yang tersedia")
        matched_meth_id = active_methods[0].id

    meth_obj = meth_by_id[matched_meth_id]

    # Token sisa yang tidak terpakai menjadi keterangan (note)
    leftover = [tok for idx, tok in enumerate(remaining_tokens) if idx not in used_token_indices]
    note = " ".join(leftover).strip() if leftover else None

    return ParsedQuickTransaction(
        type=cat_obj.type,
        amount=amount,
        category_id=cat_obj.id,
        category_name=cat_obj.name,
        method_id=meth_obj.id,
        method_name=meth_obj.name,
        note=note,
        raw_text=raw_text,
    )
