"""Handler Telegram Bot untuk Backup dan Restore Data Keuangan (V2)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.auth import admin_required, whitelist_required
from bot.keyboards import backup_list_keyboard, restore_confirmation_keyboard
from database.engine import session_scope
from services import backup
from utils.errors import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


@whitelist_required
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buat file cadangan JSON dan kirimkan sebagai dokumen ke pengguna."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _sync_export() -> tuple[Path, dict[str, Any]]:
        with session_scope(factory) as session:
            target_path = backup.export_backup(session, user_id)
            summary = backup.get_backup_summary(session, user_id, target_path)
            return target_path, summary

    try:
        path, summary = await asyncio.to_thread(_sync_export)
    except Exception as e:
        logger.exception("Gagal membuat file backup: %s", e)
        await update.effective_message.reply_text(
            f"❌ Gagal membuat cadangan: {e}", parse_mode=ParseMode.MARKDOWN
        )
        return

    s = summary.get("summary", {})
    caption = (
        "📦 *PENCADANGAN DATA SELESAI*\n\n"
        f"• File     : `{summary['filename']}`\n"
        f"• Versi    : {summary.get('version', '1.0')}\n"
        f"• Rincian Data:\n"
        f"  - Transaksi       : {s.get('transactions', 0)}\n"
        f"  - Kategori        : {s.get('categories', 0)}\n"
        f"  - Rekening/Metode : {s.get('payment_methods', 0)}\n"
        f"  - Saldo Awal      : {s.get('opening_balances', 0)}\n"
        f"  - Anggaran        : {s.get('budgets', 0)}\n"
        f"  - Transaksi Rutin : {s.get('recurring', 0)}\n"
        f"  - Alias Cepat     : {s.get('quick_aliases', 0)}\n\n"
        "💡 *Simpan file dokumen ini dengan aman.* Anda dapat memulihkannya "
        "kapan saja dengan mengirimkan file ini kembali ke bot atau lewat `/restore`."
    )

    try:
        if hasattr(update.effective_message, "reply_document"):
            with path.open("rb") as doc_file:
                await update.effective_message.reply_document(
                    document=doc_file,
                    filename=path.name,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning("Gagal mengirim dokumen backup via reply_document: %s", e)
        await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_ekspor_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ekspor seluruh data keuangan pengguna dalam arsip ZIP (JSON + CSV)."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data.get("db_user_id")
    if not user_id:
        return

    def _sync_export_zip() -> Path:
        with session_scope(factory) as session:
            return backup.export_user_backup_zip(session, user_id)

    try:
        path = await asyncio.to_thread(_sync_export_zip)
    except Exception as e:
        logger.exception("Gagal mengekspor data arsip ZIP: %s", e)
        await update.effective_message.reply_text(
            f"❌ Gagal membuat arsip data: {e}", parse_mode=ParseMode.MARKDOWN
        )
        return

    caption = (
        "📦 *ARSIP DATA PENGGUNA (ZIP)*\n\n"
        f"• File : `{path.name}`\n"
        "• Isi  : `backup.json`, `transactions.csv`, `categories.csv`, "
        "`payment_methods.csv`, `budgets.csv`\n\n"
        "💡 *Arsip ini berisi seluruh data keuangan pribadi Anda.* "
        "Anda dapat menggunakannya sebagai cadangan aman atau membukanya di Excel."
    )

    try:
        if hasattr(update.effective_message, "reply_document"):
            with path.open("rb") as doc_file:
                await update.effective_message.reply_document(
                    document=doc_file,
                    filename=path.name,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning("Gagal mengirim dokumen ZIP: %s", e)
        await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)


@admin_required
async def cmd_backup_penuh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ekspor cadangan penuh sistem (seluruh pengguna & data) khusus admin."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data.get("db_user_id")
    if not user_id:
        return

    def _sync_full_backup() -> Path:
        with session_scope(factory) as session:
            return backup.export_full_admin_backup(session, user_id)

    try:
        path = await asyncio.to_thread(_sync_full_backup)
    except Exception as e:
        logger.exception("Gagal membuat backup penuh admin: %s", e)
        await update.effective_message.reply_text(
            f"❌ Gagal membuat cadangan sistem: {e}", parse_mode=ParseMode.MARKDOWN
        )
        return

    caption = (
        "🛡️ *CADANGAN PENUH SISTEM (ADMIN)*\n\n"
        f"• File : `{path.name}`\n"
        "• Isi  : Seluruh data pengguna, transaksi, saldo, anggaran, "
        "undangan, dan konfigurasi sistem.\n\n"
        "🚨 *PERHATIAN:* Simpan file cadangan sistem ini di tempat yang aman dan terenkripsi!"
    )

    try:
        if hasattr(update.effective_message, "reply_document"):
            with path.open("rb") as doc_file:
                await update.effective_message.reply_document(
                    document=doc_file,
                    filename=path.name,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning("Gagal mengirim dokumen full backup admin: %s", e)
        await update.effective_message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan daftar cadangan di server atau panduan unggah file cadangan."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _sync_list() -> list[dict[str, Any]]:
        with session_scope(factory) as session:
            return backup.list_backups(session, user_id)

    backups = await asyncio.to_thread(_sync_list)

    if not backups:
        msg = (
            "📥 *PEMULIHAN DATA (RESTORE)*\n\n"
            "Belum ada file cadangan yang tersimpan di server.\n\n"
            "💡 *Cara Memulihkan Data:*\n"
            "Kirimkan (unggah) file dokumen `.json` atau `.zip` cadangan Anda langsung "
            "ke obrolan bot ini. Bot akan memvalidasi dan memandu proses pemulihan data."
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    text = (
        "📥 *PEMULIHAN DATA (RESTORE)*\n\n"
        f"Ditemukan {len(backups)} file cadangan di server.\n"
        "Pilih salah satu file di bawah ini untuk dipulihkan, atau *kirimkan langsung* "
        "file dokumen `.json` atau `.zip` cadangan ke chat bot ini:"
    )
    markup = backup_list_keyboard(backups)
    await update.effective_message.reply_text(
        text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def handle_backup_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangani pengiriman file dokumen .json atau .zip dari pengguna untuk pemulihan (restore)."""
    if not update.effective_message or not update.effective_message.document:
        return

    doc = update.effective_message.document
    filename = doc.file_name or "backup.json"
    lower_name = filename.lower()
    if not (lower_name.endswith(".json") or lower_name.endswith(".zip")):
        # Jika bukan file json atau zip, abaikan agar tidak mengganggu lampiran lain
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    try:
        file_obj = await doc.get_file()
        file_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        logger.warning("Gagal mengunduh file dokumen: %s", e)
        await update.effective_message.reply_text(
            f"❌ Gagal mengunduh file cadangan: {e}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    def _sync_validate() -> dict[str, Any]:
        with session_scope(factory) as session:
            return backup.validate_backup_data(session, user_id, file_bytes)

    try:
        validated = await asyncio.to_thread(_sync_validate)
    except (ValidationError, NotFoundError) as e:
        await update.effective_message.reply_text(
            f"❌ Data cadangan tidak valid: {e}", parse_mode=ParseMode.MARKDOWN
        )
        return

    context.user_data["pending_restore"] = {
        "source": validated,
        "filename": filename,
    }

    s = validated.get("summary", {})
    text = (
        "⚠️ *KONFIRMASI PEMULIHAN DATA (RESTORE)*\n\n"
        f"• File     : `{filename}`\n"
        f"• Versi    : {validated.get('version', '1.0')}\n"
        f"• Dibuat   : {validated.get('created_at', '-')}\n"
        f"• Rincian  :\n"
        f"  - Transaksi       : {s.get('transactions', 0)}\n"
        f"  - Kategori        : {s.get('categories', 0)}\n"
        f"  - Rekening/Metode : {s.get('payment_methods', 0)}\n"
        f"  - Saldo Awal      : {s.get('opening_balances', 0)}\n"
        f"  - Anggaran        : {s.get('budgets', 0)}\n"
        f"  - Transaksi Rutin : {s.get('recurring', 0)}\n"
        f"  - Alias Cepat     : {s.get('quick_aliases', 0)}\n\n"
        "🚨 *PERINGATAN:* Memulihkan cadangan akan *MENGGANTIKAN* seluruh data "
        "transaksi, anggaran, saldo awal, dan transaksi rutin saat ini dengan isi cadangan.\n\n"
        "Apakah Anda yakin ingin melanjutkan?"
    )

    await update.effective_message.reply_text(
        text, reply_markup=restore_confirmation_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def backup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangani callback pilihan dan konfirmasi pemulihan cadangan."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    if data.startswith("bkp_sel_"):
        idx = int(data.split("_")[2])

        def _sync_select() -> tuple[dict[str, Any], dict[str, Any]]:
            with session_scope(factory) as session:
                backups = backup.list_backups(session, user_id)
                if not (0 <= idx < len(backups)):
                    raise NotFoundError("Pilihan file cadangan tidak ditemukan.")
                chosen = backups[idx]
                summary = backup.get_backup_summary(session, user_id, chosen["path"])
                return chosen, summary

        try:
            chosen, summary = await asyncio.to_thread(_sync_select)
        except (NotFoundError, ValidationError) as e:
            await query.edit_message_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)
            return

        context.user_data["pending_restore"] = {
            "source": str(chosen["path"]),
            "filename": chosen["filename"],
        }

        s = summary.get("summary", {})
        text = (
            "⚠️ *KONFIRMASI PEMULIHAN DATA (RESTORE)*\n\n"
            f"• File     : `{chosen['filename']}`\n"
            f"• Dibuat   : {summary.get('created_at', '-')}\n"
            f"• Rincian  :\n"
            f"  - Transaksi       : {s.get('transactions', 0)}\n"
            f"  - Kategori        : {s.get('categories', 0)}\n"
            f"  - Rekening/Metode : {s.get('payment_methods', 0)}\n"
            f"  - Saldo Awal      : {s.get('opening_balances', 0)}\n"
            f"  - Anggaran        : {s.get('budgets', 0)}\n"
            f"  - Transaksi Rutin : {s.get('recurring', 0)}\n"
            f"  - Alias Cepat     : {s.get('quick_aliases', 0)}\n\n"
            "🚨 *PERINGATAN:* Memulihkan cadangan akan *MENGGANTIKAN* seluruh data "
            "transaksi, anggaran, saldo awal, dan transaksi rutin saat ini dengan isi cadangan.\n\n"
            "Apakah Anda yakin ingin melanjutkan pemulihan data ini?"
        )
        await query.edit_message_text(
            text, reply_markup=restore_confirmation_keyboard(), parse_mode=ParseMode.MARKDOWN
        )

    elif data == "bkp_confirm":
        pending = context.user_data.pop("pending_restore", None)
        if not pending:
            await query.edit_message_text(
                "⚠️ Sesi pemulihan data telah kedaluwarsa atau tidak ditemukan. "
                "Silakan ulangi perintah `/restore`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        source = pending["source"]
        filename = pending["filename"]

        def _sync_restore() -> dict[str, int]:
            with session_scope(factory) as session:
                return backup.restore_backup(session, user_id, source)

        try:
            res = await asyncio.to_thread(_sync_restore)
            text = (
                "✅ *PEMULIHAN DATA BERHASIL*\n\n"
                f"Data keuangan berhasil dipulihkan dari `{filename}`:\n"
                f"• Transaksi       : {res['transactions']}\n"
                f"• Kategori        : {res['categories']}\n"
                f"• Rekening/Metode : {res['payment_methods']}\n"
                f"• Saldo Awal      : {res['opening_balances']}\n"
                f"• Anggaran        : {res['budgets']}\n"
                f"• Transaksi Rutin : {res['recurring']}\n"
                f"• Alias Cepat     : {res.get('quick_aliases', 0)}\n\n"
                "Semua saldo dan catatan keuangan kini telah diperbarui."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.exception("Gagal memulihkan cadangan: %s", e)
            await query.edit_message_text(
                f"❌ Gagal memulihkan cadangan: {e}", parse_mode=ParseMode.MARKDOWN
            )

    elif data == "bkp_cancel":
        context.user_data.pop("pending_restore", None)
        await query.edit_message_text(
            "❌ Pemulihan data (restore) telah dibatalkan.", parse_mode=ParseMode.MARKDOWN
        )
