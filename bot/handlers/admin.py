"""Handler Telegram Bot untuk registrasi kode undangan dan perintah admin (V3):
- /kode <kode_undangan> : Aktivasi akun pengguna pending menggunakan kode undangan
- /buat_undangan [kode] : Buat kode undangan baru (khusus admin)
- /setujui <id>         : Setujui pengguna pending menjadi aktif (khusus admin)
- /blokir <id>          : Blokir akses pengguna (khusus admin)
- /pengguna             : Daftar pengguna & kode undangan (khusus admin)
- /status               : Ringkasan status server, SQLite, dan job scheduler (khusus admin)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import platform
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import BLOCKED_MESSAGE, REJECTION_MESSAGE, admin_required
from bot.handlers.common import extract_command_args
from bot.keyboards import main_reply_keyboard
from database import queries
from database.engine import session_scope
from models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
)
from services import users as users_service
from utils.errors import AppError

logger = logging.getLogger(__name__)


async def cmd_kode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Aktifkan akun berstatus pending menggunakan kode undangan (`/kode <kode>`)."""
    if not update.effective_message:
        return
    tg_user = update.effective_user
    if not tg_user:
        await update.effective_message.reply_text(REJECTION_MESSAGE)
        return

    args = extract_command_args(update, context)
    if not args:
        await update.effective_message.reply_text(
            "Gunakan format: `/kode <kode_undangan>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    invite_code = args[0].strip()
    name = tg_user.first_name or "Pengguna"
    factory = context.application.bot_data["session_factory"]

    def _sync_redeem() -> tuple[str, int, str]:
        with session_scope(factory) as session:
            db_user, _ = queries.register_telegram_user(session, tg_user.id, name=name)
            if db_user.status == USER_STATUS_BLOCKED:
                return "blocked", db_user.id, BLOCKED_MESSAGE
            try:
                activated = users_service.redeem_invite(session, db_user.id, invite_code)
                return "activated", activated.id, activated.name
            except AppError as exc:
                return "error", db_user.id, str(exc)

    status, db_user_id, info = await asyncio.to_thread(_sync_redeem)

    if status == "blocked":
        context.user_data.pop("db_user_id", None)
        await update.effective_message.reply_text(BLOCKED_MESSAGE)
        return

    if status == "error":
        await update.effective_message.reply_text(f"❌ {info}")
        return

    context.user_data["db_user_id"] = db_user_id
    await update.effective_message.reply_text(
        f"✅ Selamat, *{info}*! Akun Anda kini *aktif* dan data default "
        "(kategori & metode pembayaran) telah disiapkan.\n"
        "Gunakan menu di bawah atau ketik `/saldo` untuk mulai.",
        reply_markup=main_reply_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_required
async def cmd_buat_undangan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buat kode undangan baru untuk pengguna baru (khusus admin)."""
    if not update.effective_message:
        return
    admin_id: int = context.user_data["db_user_id"]
    args = extract_command_args(update, context)
    custom_code = args[0].strip() if args else None
    factory = context.application.bot_data["session_factory"]

    def _sync_create() -> tuple[bool, str]:
        with session_scope(factory) as session:
            try:
                invite = users_service.create_invite(session, admin_id, code=custom_code)
                return True, invite.code
            except AppError as exc:
                return False, str(exc)

    ok, result = await asyncio.to_thread(_sync_create)
    if not ok:
        await update.effective_message.reply_text(f"❌ {result}")
        return

    await update.effective_message.reply_text(
        f"🎟️ *Kode Undangan Berhasil Dibuat*\n\n"
        f"Kode: `{result}`\n\n"
        f"Bagikan ke calon pengguna untuk mengaktifkan akun dengan:\n"
        f"`/start {result}` atau `/kode {result}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_required
async def cmd_setujui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Setujui pengguna pending menjadi aktif berdasarkan ID DB atau ID Telegram."""
    if not update.effective_message:
        return
    admin_id: int = context.user_data["db_user_id"]
    args = extract_command_args(update, context)
    if not args or not args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text(
            "Gunakan format: `/setujui <id_pengguna_atau_id_telegram>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target_identifier = int(args[0])
    factory = context.application.bot_data["session_factory"]

    def _sync_approve() -> tuple[bool, str]:
        with session_scope(factory) as session:
            try:
                target = users_service.approve_user(session, admin_id, target_identifier)
                return (
                    True,
                    f"✅ Pengguna *{target.name}* (ID: `{target.id}`, "
                    f"Telegram: `{target.telegram_id or '-'}`) telah disetujui "
                    f"menjadi *active* dan data default-nya telah dibuat.",
                )
            except AppError as exc:
                return False, f"❌ {exc}"

    ok, msg = await asyncio.to_thread(_sync_approve)
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN if ok else None)


@admin_required
async def cmd_blokir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Blokir akses pengguna berdasarkan ID DB atau ID Telegram."""
    if not update.effective_message:
        return
    admin_id: int = context.user_data["db_user_id"]
    args = extract_command_args(update, context)
    if not args or not args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text(
            "Gunakan format: `/blokir <id_pengguna_atau_id_telegram>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target_identifier = int(args[0])
    factory = context.application.bot_data["session_factory"]

    def _sync_block() -> tuple[bool, str]:
        with session_scope(factory) as session:
            try:
                target = users_service.block_user(session, admin_id, target_identifier)
                return (
                    True,
                    f"🚫 Pengguna *{target.name}* (ID: `{target.id}`, "
                    f"Telegram: `{target.telegram_id or '-'}`) telah diblokir (*blocked*).",
                )
            except AppError as exc:
                return False, f"❌ {exc}"

    ok, msg = await asyncio.to_thread(_sync_block)
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN if ok else None)


@admin_required
async def cmd_pengguna(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan daftar pengguna dan kode undangan (khusus admin)."""
    if not update.effective_message:
        return
    admin_id: int = context.user_data["db_user_id"]
    factory = context.application.bot_data["session_factory"]

    def _sync_list() -> str:
        with session_scope(factory) as session:
            all_users = users_service.list_users(session, admin_id)
            invites = users_service.list_invites(session, admin_id)

            lines: list[str] = ["👥 *DAFTAR PENGGUNA*"]
            for u in all_users:
                lines.append(
                    f"• ID `{u.id}` | TG `{u.telegram_id or '-'}` | "
                    f"*{u.name}* | role=`{u.role}` | status=`{u.status}`"
                )

            lines.append("\n🎟️ *KODE UNDANGAN ANDA*")
            if not invites:
                lines.append("• _(Belum ada kode undangan)_")
            else:
                for inv in invites[:15]:
                    used_txt = (
                        f"Terpakai (oleh user #{inv.used_by_user_id})"
                        if inv.is_used
                        else "Tersedia"
                    )
                    lines.append(f"• `{inv.code}` — {used_txt}")
            return "\n".join(lines)

    text = await asyncio.to_thread(_sync_list)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@admin_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan status operasional bot, server, SQLite, dan job scheduler (khusus admin)."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    job_queue = getattr(context.application, "job_queue", None)
    active_jobs_count = len(job_queue.jobs()) if job_queue else 0

    def _sync_status() -> str:
        db_path = Path(config.DB_PATH)
        db_size_kb = (db_path.stat().st_size / 1024) if db_path.is_file() else 0.0

        with session_scope(factory) as session:
            diag = queries.get_database_diagnostics(session)
            integrity = diag["integrity"]
            journal_mode = diag["journal_mode"]

            all_users = queries.list_users(session)
            active_u = sum(1 for u in all_users if u.status == USER_STATUS_ACTIVE)
            pending_u = sum(1 for u in all_users if u.status == "pending")
            blocked_u = sum(1 for u in all_users if u.status == USER_STATUS_BLOCKED)

            total_tx = queries.count_all_transactions(session)
            total_budgets = queries.count_all_budgets(session)
            total_rec = queries.count_all_recurring(session)

        backups = []
        if config.BACKUPS_DIR.is_dir():
            backups = sorted(
                list(config.BACKUPS_DIR.glob("*.db"))
                + list(config.BACKUPS_DIR.glob("*.zip"))
                + list(config.BACKUPS_DIR.glob("*.json")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        latest_backup_str = (
            f"`{backups[0].name}` ({backups[0].stat().st_size / 1024:.1f} KB)"
            if backups
            else "_(Belum ada backup)_"
        )

        now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return (
            "📊 *STATUS SISTEM & OPERASIONAL*\n\n"
            "🟢 *Kondisi Sistem*: Sehat (Healthy / Running)\n"
            f"• Waktu Server : `{now_str}` ({config.TIMEZONE_NAME})\n"
            f"• Python       : `{platform.python_version()}`\n"
            f"• Platform     : `{platform.system()} {platform.machine()}`\n\n"
            "💾 *Database SQLite*:\n"
            f"• Lokasi       : `{config.DB_PATH}`\n"
            f"• Ukuran File  : `{db_size_kb:.1f} KB`\n"
            f"• Integritas   : `{integrity}`\n"
            f"• Mode Jurnal  : `{journal_mode.upper()}`\n\n"
            "👥 *Pengguna*:\n"
            f"• Total Pengguna : `{len(all_users)}`\n"
            f"  - Aktif: `{active_u}` | Pending: `{pending_u}` | Blokir: `{blocked_u}`\n\n"
            "📈 *Data Keuangan*:\n"
            f"• Total Transaksi : `{total_tx}`\n"
            f"• Total Budget    : `{total_budgets}`\n"
            f"• Transaksi Rutin : `{total_rec}`\n\n"
            "📦 *Cadangan (Backups)*:\n"
            f"• Total File Cadangan : `{len(backups)}` file\n"
            f"• Cadangan Terbaru    : {latest_backup_str}\n\n"
            "⏰ *Penjadwal (JobQueue)*:\n"
            f"• Job Aktif Terdaftar : `{active_jobs_count}` job"
        )

    msg = await asyncio.to_thread(_sync_status)
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
