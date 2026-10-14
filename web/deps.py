"""Dependensi dan helper untuk router FastAPI Web Dashboard."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from fastapi import HTTPException, Request, status
from telegram.constants import ParseMode

from database.engine import session_scope
from models import User
from services import auth_web

logger = logging.getLogger(__name__)


def get_session_factory(request: Request) -> Any:
    """Ambil factory session database dari state aplikasi."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise RuntimeError("Database session factory belum diinisialisasi pada app.state.")
    return factory


def get_current_user_optional(request: Request) -> User | None:
    """Ambil pengguna terotentikasi dari cookie sesi jika ada."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    factory = get_session_factory(request)
    try:
        with session_scope(factory) as session:
            return auth_web.get_authenticated_user(session, int(user_id))
    except Exception:
        request.session.clear()
        return None


def get_current_user(request: Request) -> User:
    """Ambil pengguna terotentikasi, atau lemparkan 401 jika belum login."""
    user = get_current_user_optional(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Silakan login terlebih dahulu untuk mengakses halaman ini.",
        )
    return user


def require_admin(request: Request) -> User:
    """Pastikan pengguna terautentikasi dan memiliki peran 'admin' (Fase W5).

    Jika belum login, lempar HTTP 401.
    Jika peran pengguna bukan 'admin', lempar HTTP 403 Forbidden.
    """
    user = get_current_user_optional(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Silakan login terlebih dahulu untuk mengakses halaman ini.",
        )
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak. Halaman ini hanya dapat diakses oleh Administrator.",
        )
    return user


async def send_telegram_otp(request: Request, telegram_id: int, code: str) -> bool:
    """Kirimkan kode OTP ke chat Telegram pengguna.

    Kode OTP TIDAK PERNAH dicatat ke log demi privasi dan keamanan.
    Mendukung injeksi mock sender untuk pengujian via request.app.state.bot_sender.
    """
    app = request.app
    custom_sender = getattr(app.state, "bot_sender", None)
    if custom_sender is not None:
        res = custom_sender(telegram_id, code)
        if inspect.isawaitable(res):
            await res
        return True

    bot_app = getattr(app.state, "bot_app", None)
    if bot_app is not None and getattr(bot_app, "bot", None) is not None:
        msg = (
            "🔐 *Kode Verifikasi Web Dashboard*\n\n"
            f"Kode OTP Anda: `{code}`\n\n"
            "⏳ Berlaku selama 5 menit.\n"
            "⚠️ Jangan pernah berikan kode ini kepada siapa pun."
        )
        try:
            await bot_app.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            return True
        except Exception as exc:
            logger.warning("Gagal mengirimkan OTP ke Telegram ID: %s", exc)
            return False

    logger.warning("Bot Telegram tidak aktif di lifespan; OTP tidak dapat dikirimkan.")
    return False
