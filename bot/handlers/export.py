"""Handler Telegram Bot untuk Export Data & Laporan Keuangan (CSV, Excel, PDF) (V2)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import whitelist_required
from bot.keyboards import export_menu_keyboard
from database import queries
from database.engine import session_scope
from services import export
from utils.dates import today

logger = logging.getLogger(__name__)


def _resolve_period_dates(
    period_key: str, timezone_name: str = config.TIMEZONE_NAME
) -> tuple[date | None, date | None, str]:
    """Tentukan rentang tanggal dan label berdasarkan kunci periode serta zona waktu."""
    t = today(timezone_name)
    if period_key == "month":
        return date(t.year, t.month, 1), t, "Bulan Ini"
    if period_key == "week":
        start = t - timedelta(days=t.weekday())
        end = start + timedelta(days=6)
        return start, end, "Minggu Ini"
    return None, None, "Semua Periode"


async def _run_and_send_export(
    message: Any,
    factory: Any,
    user_id: int,
    fmt: str,
    period_key: str = "month",
) -> None:
    """Jalankan fungsi ekspor di thread terpisah dan kirimkan dokumen ke chat Telegram."""

    def _sync_export() -> tuple[Path, str, str]:
        with session_scope(factory) as session:
            user = queries.get_user(session, user_id)
            tz_name = user.timezone if user else config.TIMEZONE_NAME
            start_date, end_date, period_label = _resolve_period_dates(period_key, tz_name)
            if fmt == "csv":
                return (
                    export.export_csv(session, user_id, start=start_date, end=end_date),
                    "CSV",
                    period_label,
                )
            if fmt in ("xlsx", "excel"):
                return (
                    export.export_excel(session, user_id, start=start_date, end=end_date),
                    "Excel (.xlsx)",
                    period_label,
                )
            return (
                export.export_pdf(session, user_id, start=start_date, end=end_date),
                "PDF (.pdf)",
                period_label,
            )

    try:
        target_path, fmt_label, period_label = await asyncio.to_thread(_sync_export)
    except Exception as err:
        logger.exception("Gagal mengekspor data (%s): %s", fmt, err)
        await message.reply_text(
            f"❌ Gagal membuat file ekspor: {err}", parse_mode=ParseMode.MARKDOWN
        )
        return

    caption = (
        "📤 *EKSPOR DATA BERHASIL*\n\n"
        f"• Format  : *{fmt_label}*\n"
        f"• Periode : *{period_label}*\n"
        f"• File    : `{target_path.name}`"
    )

    try:
        if hasattr(message, "reply_document"):
            with target_path.open("rb") as doc_file:
                await message.reply_document(
                    document=doc_file,
                    filename=target_path.name,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as err:
        logger.warning("Gagal mengirim dokumen ekspor via reply_document: %s", err)
        await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan menu interaktif ekspor data atau langsung ekspor jika diberi argumen."""
    if not update.effective_message:
        return

    args = context.args or []
    if args:
        fmt_arg = args[0].strip().lower()
        period_arg = args[1].strip().lower() if len(args) > 1 else "month"
        if fmt_arg in ("csv", "xlsx", "excel", "pdf"):
            p_key = (
                "all"
                if period_arg in ("all", "semua")
                else ("week" if period_arg in ("week", "minggu") else "month")
            )
            factory = context.application.bot_data["session_factory"]
            user_id = context.user_data["db_user_id"]
            await _run_and_send_export(update.effective_message, factory, user_id, fmt_arg, p_key)
            return

    text = (
        "📤 *EKSPOR DATA & LAPORAN KEUANGAN*\n\n"
        "Pilih format file (*CSV*, *Excel*, atau *PDF*) dan cakupan periode "
        "yang ingin Anda unduh:"
    )
    await update.effective_message.reply_text(
        text, reply_markup=export_menu_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def cmd_export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Perintah cepat ekspor ke CSV (default bulan ini)."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    await _run_and_send_export(update.effective_message, factory, user_id, "csv", "month")


@whitelist_required
async def cmd_export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Perintah cepat ekspor ke Excel (.xlsx) (default bulan ini)."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    await _run_and_send_export(update.effective_message, factory, user_id, "xlsx", "month")


@whitelist_required
async def cmd_export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Perintah cepat ekspor ke PDF (.pdf) (default bulan ini)."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    await _run_and_send_export(update.effective_message, factory, user_id, "pdf", "month")


@whitelist_required
async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangani pilihan tombol inline ekspor (`exp_<fmt>_<period>`)."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    await query.answer()
    parts = query.data.split("_")
    if len(parts) != 3 or parts[0] != "exp":
        return

    _, fmt, period_key = parts
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    await _run_and_send_export(query.message, factory, user_id, fmt, period_key)
