"""Autentikasi status pengguna Telegram (V3) dan pemetaan ke database."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

import config
from database import queries
from database.engine import session_scope
from database.seed import seed_defaults
from models import (
    ROLE_ADMIN,
    ROLE_USER,
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
)
from utils.rate_limiter import global_rate_limiter

logger = logging.getLogger(__name__)

REJECTION_MESSAGE: str = "Maaf, Anda tidak memiliki akses ke bot ini."
PENDING_MESSAGE: str = (
    "⏳ Akun Anda masih berstatus pending (menunggu aktivasi).\n"
    "Gunakan /start <kode_undangan> atau /kode <kode_undangan>, "
    "atau tunggu persetujuan dari admin."
)
BLOCKED_MESSAGE: str = "🚫 Maaf, akun Anda telah diblokir dan tidak dapat mengakses bot ini."
ADMIN_ONLY_MESSAGE: str = "⛔ Perintah ini hanya dapat dijalankan oleh admin."
RATE_LIMIT_MESSAGE: str = (
    "⏳ Terlalu banyak permintaan. Silakan tunggu beberapa saat sebelum mencoba lagi."
)


def is_allowed(telegram_id: int | None) -> bool:
    """Periksa apakah ID Telegram terdaftar dalam daftar admin awal config.ALLOWED_TELEGRAM_IDS."""
    if telegram_id is None:
        return False
    return telegram_id in config.ALLOWED_TELEGRAM_IDS


async def resolve_db_user_id(factory: Any, telegram_id: int, name: str = "User") -> int:
    """Petakan telegram_id ke users.id di database, mengisi users.telegram_id jika belum ada."""

    def _sync() -> int:
        with session_scope(factory) as session:
            user = queries.ensure_telegram_user(session, telegram_id, name=name)
            if user.status == USER_STATUS_ACTIVE:
                seed_defaults(session, user.id)
            return user.id

    return await asyncio.to_thread(_sync)


async def check_and_resolve_user(
    factory: Any, telegram_id: int, name: str = "User"
) -> tuple[str, int | None, str]:
    """Periksa status pengguna di database dan pastikan data default terisi bila aktif.

    Returns:
        Tuple `(status, db_user_id, role)` dengan `status` bernilai
        `'active'`, `'pending'`, `'blocked'`, atau `'unregistered'`.
    """

    def _sync() -> tuple[str, int | None, str]:
        with session_scope(factory) as session:
            user = queries.get_user_by_telegram_id(session, telegram_id)
            if user is None:
                if telegram_id in config.ALLOWED_TELEGRAM_IDS:
                    user = queries.ensure_telegram_user(session, telegram_id, name=name)
                    seed_defaults(session, user.id)
                    return user.status, user.id, user.role
                return "unregistered", None, ROLE_USER

            if (
                telegram_id in config.ALLOWED_TELEGRAM_IDS
                and user.role != ROLE_ADMIN
                and user.status != USER_STATUS_BLOCKED
            ):
                user.role = ROLE_ADMIN
                user.status = USER_STATUS_ACTIVE
                session.flush()

            if user.status == USER_STATUS_ACTIVE:
                seed_defaults(session, user.id)

            return user.status, user.id, user.role

    return await asyncio.to_thread(_sync)


def whitelist_required(handler_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator untuk memastikan hanya pengguna berstatus `active` yang bisa mengakses bot."""

    @wraps(handler_fn)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> Any:
        tg_user = update.effective_user
        if not tg_user:
            logger.warning("Akses ditolak: effective_user kosong")
            if update.effective_message:
                await update.effective_message.reply_text(REJECTION_MESSAGE)
            return None

        if not global_rate_limiter.is_allowed(tg_user.id):
            logger.warning("Rate limit terlampaui untuk telegram_id=%d", tg_user.id)
            if update.effective_message:
                await update.effective_message.reply_text(RATE_LIMIT_MESSAGE)
            return None

        factory = context.application.bot_data["session_factory"]
        name = tg_user.first_name or "User"
        status, db_user_id, _role = await check_and_resolve_user(factory, tg_user.id, name=name)

        if status == USER_STATUS_ACTIVE and db_user_id is not None:
            context.user_data["db_user_id"] = db_user_id
            return await handler_fn(update, context, *args, **kwargs)

        context.user_data.pop("db_user_id", None)
        logger.warning("Akses ditolak untuk telegram_id=%d (status=%s)", tg_user.id, status)
        if update.effective_message:
            if status == USER_STATUS_PENDING:
                await update.effective_message.reply_text(PENDING_MESSAGE)
            elif status == USER_STATUS_BLOCKED:
                await update.effective_message.reply_text(BLOCKED_MESSAGE)
            else:
                await update.effective_message.reply_text(REJECTION_MESSAGE)
        return None

    return wrapper


active_user_required = whitelist_required


def admin_required(handler_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator untuk memastikan pemanggil adalah pengguna aktif dengan `role == 'admin'`."""

    @wraps(handler_fn)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> Any:
        tg_user = update.effective_user
        if not tg_user:
            if update.effective_message:
                await update.effective_message.reply_text(REJECTION_MESSAGE)
            return None

        if not global_rate_limiter.is_allowed(tg_user.id):
            logger.warning("Rate limit terlampaui untuk telegram_id=%d", tg_user.id)
            if update.effective_message:
                await update.effective_message.reply_text(RATE_LIMIT_MESSAGE)
            return None

        factory = context.application.bot_data["session_factory"]
        name = tg_user.first_name or "User"
        status, db_user_id, role = await check_and_resolve_user(factory, tg_user.id, name=name)

        if status != USER_STATUS_ACTIVE or db_user_id is None:
            context.user_data.pop("db_user_id", None)
            if update.effective_message:
                if status == USER_STATUS_PENDING:
                    await update.effective_message.reply_text(PENDING_MESSAGE)
                elif status == USER_STATUS_BLOCKED:
                    await update.effective_message.reply_text(BLOCKED_MESSAGE)
                else:
                    await update.effective_message.reply_text(REJECTION_MESSAGE)
            return None

        if role != ROLE_ADMIN:
            if update.effective_message:
                await update.effective_message.reply_text(ADMIN_ONLY_MESSAGE)
            return None

        context.user_data["db_user_id"] = db_user_id
        return await handler_fn(update, context, *args, **kwargs)

    return wrapper
