"""Layanan autentikasi Web Dashboard via OTP Telegram (Fase W).

Aturan keamanan:
- Layanan ini bersifat sinkron dan beroperasi melalui `session_scope`.
- Kode OTP murni, token, atau kredensial TIDAK PERNAH dicatat ke log.
- Pengguna berstatus `pending` atau `blocked` ditolak secara eksplisit.
- Diterapkan rate limit pembuatan OTP per menit untuk mencegah spam.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

from sqlalchemy.orm import Session

import config
from database import queries
from models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
    User,
    WebLoginOtp,
)
from utils.errors import NotFoundError, ValidationError


def _utcnow() -> dt.datetime:
    """Waktu saat ini dalam UTC (naive, serasi dengan penyimpanan DB)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _hash_otp(code: str, user_id: int) -> str:
    """Hasilkan hash HMAC-SHA256 dari kode OTP untuk disimpan di DB."""
    key = f"{config.SECRET_KEY}:{user_id}".encode()
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_telegram_user_for_login(session: Session, telegram_id: int) -> User:
    """Validasi keberadaan dan status akun pengguna berdasarkan ID Telegram."""
    if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
        raise ValidationError("ID Telegram harus berupa angka positif.")

    user = queries.get_user_by_telegram_id(session, telegram_id)
    if user is None:
        raise NotFoundError("ID Telegram tidak terdaftar di sistem.")

    if user.status == USER_STATUS_PENDING:
        raise ValidationError(
            "Akun Anda masih berstatus pending dan menunggu persetujuan administrator."
        )
    if user.status == USER_STATUS_BLOCKED:
        raise ValidationError("Akun Anda telah diblokir. Hubungi administrator.")
    if user.status != USER_STATUS_ACTIVE:
        raise ValidationError(f"Akun Anda tidak aktif ({user.status}).")

    return user


def create_login_otp(session: Session, user_id: int) -> tuple[WebLoginOtp, str]:
    """Buat dan simpan kode OTP baru untuk user_id.

    Mengembalikan (record_otp, plain_code).
    Peringatan: plain_code hanya untuk dikirim ke chat Telegram, jangan dicatat ke log!
    """
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    if user.status != USER_STATUS_ACTIVE:
        raise ValidationError("Akun tidak aktif.")

    now = _utcnow()
    one_minute_ago = now - dt.timedelta(minutes=1)
    recent_count = queries.count_recent_otps(session, user_id, one_minute_ago)
    if recent_count >= config.WEB_OTP_RATE_LIMIT_PER_MINUTE:
        raise ValidationError(
            "Terlalu banyak permintaan OTP. Silakan tunggu 1 menit sebelum mencoba lagi."
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _hash_otp(code, user_id)
    expires_at = now + dt.timedelta(minutes=config.WEB_OTP_EXPIRY_MINUTES)

    otp = queries.create_web_login_otp(
        session,
        user_id=user_id,
        code_hash=code_hash,
        expires_at=expires_at,
    )
    return otp, code


def request_login_otp(session: Session, telegram_id: int) -> tuple[User, str]:
    """Alur lengkap langkah 1 login: validasi ID Telegram dan hasilkan kode OTP."""
    user = validate_telegram_user_for_login(session, telegram_id)
    _, code = create_login_otp(session, user.id)
    return user, code


def verify_login_otp(session: Session, user_id: int, code: str) -> User:
    """Alur langkah 2 login: verifikasi kode OTP dan masa berlaku."""
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    if user.status != USER_STATUS_ACTIVE:
        raise ValidationError("Akun tidak aktif.")

    clean_code = (code or "").strip()
    if not clean_code or len(clean_code) != 6 or not clean_code.isdigit():
        raise ValidationError("Kode OTP harus terdiri dari 6 digit angka.")

    now = _utcnow()
    otp = queries.get_latest_active_otp(session, user_id, now)
    if otp is None:
        raise ValidationError(
            "Kode OTP tidak ditemukan atau sudah kedaluwarsa. Silakan minta kode baru."
        )

    expected_hash = _hash_otp(clean_code, user_id)
    if not hmac.compare_digest(otp.code_hash, expected_hash):
        raise ValidationError("Kode OTP salah. Silakan periksa kembali pesan Telegram Anda.")

    queries.mark_otp_used(session, otp.id, now)
    return user


def get_authenticated_user(session: Session, user_id: int) -> User:
    """Ambil data pengguna terotentikasi dan pastikan status masih aktif."""
    user = queries.get_user(session, user_id)
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")
    if user.status != USER_STATUS_ACTIVE:
        raise ValidationError("Akun tidak aktif atau telah diblokir.")
    return user
