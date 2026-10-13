"""Layanan manajemen pengguna, registrasi, kode undangan, dan otorisasi admin (V3).

Semua fungsi mengikuti kontrak arsitektur `(session, user_id, ...)`:
- Pembuatan kode undangan, persetujuan pengguna, pemblokiran, dan daftar pengguna
  memverifikasi bahwa `user_id` pemanggil adalah admin aktif (`require_admin`).
- Aktivasi pengguna (baik lewat kode undangan maupun persetujuan admin) otomatis
  membuat data default (kategori, metode pembayaran, kategori sistem) milik pengguna
  tersebut via `seed_defaults`.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy.orm import Session

from database import queries
from database.seed import seed_defaults
from models import (
    ROLE_ADMIN,
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUSES,
    Invite,
    User,
)
from services.common import require_admin
from utils.errors import NotFoundError, ValidationError

MAX_INVITE_CODE_LENGTH = 50


def _utcnow() -> dt.datetime:
    """Waktu sekarang dalam UTC (naive, sesuai penyimpanan DB)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _generate_unique_code(session: Session) -> str:
    """Hasilkan kode undangan acak 8 karakter yang belum pernah dipakai."""
    for _ in range(20):
        candidate = secrets.token_hex(4).upper()
        if queries.get_invite_by_code(session, candidate) is None:
            return candidate
    raise ValidationError("Gagal menghasilkan kode undangan unik. Silakan coba lagi.")


def _resolve_target_user(session: Session, target_identifier: int) -> User:
    """Cari pengguna berdasarkan `users.id` atau `users.telegram_id`."""
    if not isinstance(target_identifier, int) or isinstance(target_identifier, bool):
        raise ValidationError("ID pengguna harus berupa angka bulat.")
    target = queries.get_user(session, target_identifier)
    if target is None:
        target = queries.get_user_by_telegram_id(session, target_identifier)
    if target is None:
        raise NotFoundError("Pengguna target tidak ditemukan.")
    return target


def create_invite(
    session: Session,
    user_id: int,
    *,
    code: str | None = None,
    expires_in_days: int | None = None,
) -> Invite:
    """Buat kode undangan baru (khusus admin aktif)."""
    require_admin(session, user_id)

    if code is not None:
        clean_code = code.strip()
        if not clean_code:
            raise ValidationError("Kode undangan tidak boleh kosong.")
        if len(clean_code) > MAX_INVITE_CODE_LENGTH:
            raise ValidationError(f"Kode undangan maksimal {MAX_INVITE_CODE_LENGTH} karakter.")
        if any(ch.isspace() for ch in clean_code):
            raise ValidationError("Kode undangan tidak boleh mengandung spasi.")
        if queries.get_invite_by_code(session, clean_code) is not None:
            raise ValidationError(f"Kode undangan '{clean_code}' sudah terdaftar.")
    else:
        clean_code = _generate_unique_code(session)

    expires_at: dt.datetime | None = None
    if expires_in_days is not None:
        if (
            not isinstance(expires_in_days, int)
            or isinstance(expires_in_days, bool)
            or expires_in_days <= 0
        ):
            raise ValidationError("Masa berlaku undangan (hari) harus berupa angka positif.")
        expires_at = _utcnow() + dt.timedelta(days=expires_in_days)

    invite = Invite(
        user_id=user_id,
        code=clean_code,
        is_used=False,
        expires_at=expires_at,
    )
    session.add(invite)
    session.flush()
    return invite


def list_invites(session: Session, user_id: int, *, include_used: bool = True) -> list[Invite]:
    """Daftar kode undangan yang dibuat oleh admin `user_id`."""
    require_admin(session, user_id)
    return queries.list_invites(session, user_id, include_used=include_used)


def redeem_invite(session: Session, user_id: int, code: str) -> User:
    """Tukarkan kode undangan untuk mengaktifkan pengguna `user_id` dan mengisi data default."""
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    if user.status == USER_STATUS_BLOCKED:
        raise ValidationError("Akun Anda telah diblokir dan tidak dapat menggunakan kode undangan.")
    if user.status == USER_STATUS_ACTIVE:
        raise ValidationError("Akun Anda sudah aktif.")

    clean_code = (code or "").strip()
    if not clean_code:
        raise ValidationError("Kode undangan tidak boleh kosong.")

    invite = queries.get_invite_by_code(session, clean_code)
    if invite is None:
        raise NotFoundError("Kode undangan tidak ditemukan atau tidak valid.")
    if invite.is_used:
        raise ValidationError("Kode undangan sudah pernah digunakan.")
    if invite.expires_at is not None and invite.expires_at < _utcnow():
        raise ValidationError("Kode undangan sudah kedaluwarsa.")

    creator = queries.get_user(session, invite.user_id)
    if creator is None or creator.role != ROLE_ADMIN or creator.status != USER_STATUS_ACTIVE:
        raise ValidationError("Kode undangan tidak valid.")

    invite.is_used = True
    invite.used_by_user_id = user.id
    invite.used_at = _utcnow()

    user.status = USER_STATUS_ACTIVE
    session.flush()

    seed_defaults(session, user.id)
    session.flush()
    return user


def approve_user(session: Session, user_id: int, target_identifier: int) -> User:
    """Setujui pengguna menjadi `active` (khusus admin) dan buat data default-nya."""
    require_admin(session, user_id)
    target = _resolve_target_user(session, target_identifier)

    target.status = USER_STATUS_ACTIVE
    session.flush()

    seed_defaults(session, target.id)
    session.flush()
    return target


def block_user(session: Session, user_id: int, target_identifier: int) -> User:
    """Blokir akses pengguna (khusus admin). Admin tidak dapat memblokir dirinya sendiri."""
    require_admin(session, user_id)
    target = _resolve_target_user(session, target_identifier)

    if target.id == user_id:
        raise ValidationError("Admin tidak dapat memblokir akunnya sendiri.")

    target.status = USER_STATUS_BLOCKED
    session.flush()
    return target


def list_users(session: Session, user_id: int, *, status: str | None = None) -> list[User]:
    """Daftar seluruh pengguna aplikasi (khusus admin)."""
    require_admin(session, user_id)
    if status is not None and status not in USER_STATUSES:
        raise ValidationError(f"Filter status tidak valid: {status!r}.")
    return queries.list_users(session, status=status)


def delete_user_account(session: Session, user_id: int) -> None:
    """Hapus akun pengguna dan seluruh data keuangannya secara permanen.

    Data pengguna lain dijamin tidak akan terpengaruh.
    """
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    queries.delete_user_permanently(session, user_id)
