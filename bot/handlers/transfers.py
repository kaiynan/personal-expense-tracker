"""Percakapan bertahap untuk transfer antar metode pembayaran."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from bot.auth import whitelist_required
from bot.keyboards import (
    confirmation_keyboard,
    methods_inline_keyboard,
    skip_note_keyboard,
    today_date_keyboard,
    zero_admin_fee_keyboard,
)
from database.engine import session_scope
from services import balances, transfers
from utils.dates import today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date, validate_amount, validate_note

logger = logging.getLogger(__name__)

# State percakapan transfer bertahap
(
    STATE_AMOUNT,
    STATE_FROM_METHOD,
    STATE_TO_METHOD,
    STATE_ADMIN_FEE,
    STATE_DATE,
    STATE_NOTE,
    STATE_CONFIRM,
) = range(7)


@whitelist_required
async def start_add_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mulai percakapan transfer: minta nominal uang yang akan dipindahkan."""
    context.user_data["new_trf"] = {}
    msg = "Ketik nominal transfer dalam rupiah (angka bulat positif, contoh: `500000`):"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg)
    elif update.effective_message:
        await update.effective_message.reply_text(msg)
    return STATE_AMOUNT


async def enter_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi input nominal transfer."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_AMOUNT

    text = update.effective_message.text.strip()
    try:
        amount = validate_amount(text)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Masukan tidak valid: {err}\nSilakan masukkan nominal berupa angka positif:"
        )
        return STATE_AMOUNT

    context.user_data["new_trf"]["amount"] = amount
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_methods() -> list[Any]:
        with session_scope(factory) as session:
            return [m for m in balances.get_method_balances(session, user_id) if not m.is_archived]

    method_list = await asyncio.to_thread(_get_methods)
    if len(method_list) < 2:
        await update.effective_message.reply_text(
            "❌ Dibutuhkan minimal 2 metode pembayaran aktif untuk melakukan transfer."
        )
        context.user_data.pop("new_trf", None)
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"Nominal Transfer: *{format_rupiah(amount)}*\n\n"
        "Pilih metode pembayaran *asal* (sumber dana):",
        reply_markup=methods_inline_keyboard(method_list),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_FROM_METHOD


async def select_from_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Simpan metode asal lalu minta pilihan metode tujuan."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    from_mid = int(query.data.removeprefix("met_"))
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_from_name_and_all() -> tuple[str, list[Any]]:
        with session_scope(factory) as session:
            all_m = [m for m in balances.get_method_balances(session, user_id) if not m.is_archived]
            name = next((m.name for m in all_m if m.method_id == from_mid), "Metode")
            return name, all_m

    from_name, all_methods = await asyncio.to_thread(_get_from_name_and_all)
    context.user_data["new_trf"]["from_method_id"] = from_mid
    context.user_data["new_trf"]["from_name"] = from_name

    await query.edit_message_text(
        f"Metode Asal: *{from_name}*\n\nPilih metode pembayaran *tujuan*:",
        reply_markup=methods_inline_keyboard(all_methods, exclude_id=from_mid),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_TO_METHOD


async def select_to_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Simpan metode tujuan lalu minta biaya admin (jika ada)."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    to_mid = int(query.data.removeprefix("met_"))
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_to_name() -> str:
        with session_scope(factory) as session:
            for m in balances.get_method_balances(session, user_id):
                if m.method_id == to_mid:
                    return m.name
            return "Metode"

    to_name = await asyncio.to_thread(_get_to_name)
    context.user_data["new_trf"]["to_method_id"] = to_mid
    context.user_data["new_trf"]["to_name"] = to_name

    await query.edit_message_text(
        f"Metode Tujuan: *{to_name}*\n\n"
        "Masukkan biaya admin transfer dalam rupiah (jika ada, contoh: `2500`) "
        "atau tekan tombol di bawah jika tanpa biaya:",
        reply_markup=zero_admin_fee_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_ADMIN_FEE


async def fee_zero_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set biaya admin transfer menjadi 0."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    context.user_data["new_trf"]["admin_fee"] = 0
    await query.edit_message_text(
        "Biaya Admin: *Rp0*\n\n"
        "Masukkan tanggal transfer (format `DD-MM-YYYY`, contoh: `12-10-2026`) "
        "atau tekan tombol di bawah:",
        reply_markup=today_date_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_DATE


async def enter_admin_fee_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi input teks biaya admin transfer."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_ADMIN_FEE

    raw = update.effective_message.text.strip()
    try:
        # Boleh 0
        fee = 0 if raw == "0" else validate_amount(raw)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Biaya admin tidak valid: {err}\n"
            "Masukkan angka bulat (contoh: 2500) atau pilih tombol:",
            reply_markup=zero_admin_fee_keyboard(),
        )
        return STATE_ADMIN_FEE

    context.user_data["new_trf"]["admin_fee"] = fee
    await update.effective_message.reply_text(
        f"Biaya Admin: *{format_rupiah(fee)}*\n\n"
        "Masukkan tanggal transfer (format `DD-MM-YYYY`) atau gunakan tombol:",
        reply_markup=today_date_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_DATE


async def date_today_trf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gunakan tanggal hari ini menurut zona waktu pengguna untuk transfer."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    factory = context.application.bot_data.get("session_factory")
    user_id = context.user_data.get("db_user_id")

    def _get_today_str() -> str:
        if factory and user_id:
            with session_scope(factory) as session:
                from database import queries

                user = queries.get_user(session, user_id)
                if user:
                    return today(user.timezone).strftime("%d-%m-%Y")
        return today(config.TIMEZONE_NAME).strftime("%d-%m-%Y")

    today_str = await asyncio.to_thread(_get_today_str)
    context.user_data["new_trf"]["date"] = today_str

    await query.edit_message_text(
        f"Tanggal: *{today_str}*\n\nMasukkan keterangan transfer (opsional) atau lewati:",
        reply_markup=skip_note_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_NOTE


async def enter_trf_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi teks tanggal transfer."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_DATE

    raw = update.effective_message.text.strip()
    try:
        _ = parse_date(raw)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Tanggal tidak valid: {err}\nMasukkan format `DD-MM-YYYY` atau gunakan tombol:",
            reply_markup=today_date_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_DATE

    context.user_data["new_trf"]["date"] = raw
    await update.effective_message.reply_text(
        f"Tanggal: *{raw}*\n\n"
        "Masukkan keterangan transfer (opsional) atau tekan tombol untuk lewati:",
        reply_markup=skip_note_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_NOTE


async def note_skip_trf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Lewati keterangan transfer."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    context.user_data["new_trf"]["note"] = ""
    summary = _format_trf_summary(context.user_data["new_trf"])
    await query.edit_message_text(
        summary,
        reply_markup=confirmation_keyboard("confirm_trf_save"),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CONFIRM


async def enter_trf_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi teks keterangan transfer."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_NOTE

    raw = update.effective_message.text.strip()
    try:
        clean_note = validate_note(raw)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Keterangan tidak valid: {err}\nSilakan masukkan keterangan yang lebih ringkas:",
            reply_markup=skip_note_keyboard(),
        )
        return STATE_NOTE

    context.user_data["new_trf"]["note"] = clean_note
    summary = _format_trf_summary(context.user_data["new_trf"])
    await update.effective_message.reply_text(
        summary,
        reply_markup=confirmation_keyboard("confirm_trf_save"),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CONFIRM


def _format_trf_summary(trf: dict[str, Any]) -> str:
    fee_str = format_rupiah(trf["admin_fee"]) if trf["admin_fee"] else "Rp0"
    note_disp = trf.get("note") or "_(tanpa keterangan)_"
    return (
        "🔍 *KONFIRMASI TRANSFER DANA*\n\n"
        f"• *Metode Asal*   : {trf['from_name']}\n"
        f"• *Metode Tujuan* : {trf['to_name']}\n"
        f"• *Nominal*       : `{format_rupiah(trf['amount'])}`\n"
        f"• *Biaya Admin*   : `{fee_str}`\n"
        f"• *Tanggal*       : {trf['date']}\n"
        f"• *Keterangan*    : {note_disp}\n\n"
        "Apakah data transfer di atas sudah benar?"
    )


async def confirm_trf_save_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Eksekusi transfer antar metode ke database."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    trf_data = context.user_data.get("new_trf")
    if not trf_data:
        await query.edit_message_text("❌ Data transfer kedaluwarsa.")
        return ConversationHandler.END

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _execute() -> tuple[int, int | None, list[str]]:
        with session_scope(factory) as session:
            res = transfers.add_transfer(
                session,
                user_id,
                amount=trf_data["amount"],
                from_method_id=trf_data["from_method_id"],
                to_method_id=trf_data["to_method_id"],
                on=trf_data["date"],
                admin_fee=trf_data["admin_fee"],
                note=trf_data.get("note"),
            )
            fee_id = res.admin_fee_transaction.id if res.admin_fee_transaction else None
            return res.transfer.id, fee_id, list(res.warnings)

    try:
        trf_id, fee_id, warnings = await asyncio.to_thread(_execute)
        logger.info(
            "Transfer berhasil dieksekusi untuk user_id=%d, transfer_id=%d, fee_id=%s",
            user_id,
            trf_id,
            str(fee_id),
        )
    except ValidationError as err:
        await query.edit_message_text(f"❌ Gagal memproses transfer: {err}")
        context.user_data.pop("new_trf", None)
        return ConversationHandler.END

    lines = [f"✅ *Transfer berhasil diproses!* (`#{trf_id}`)\n"]
    lines.append(f"• *Dari*        : {trf_data['from_name']}")
    lines.append(f"• *Ke*          : {trf_data['to_name']}")
    lines.append(f"• *Nominal*     : `{format_rupiah(trf_data['amount'])}`")
    fee_val = trf_data["admin_fee"]
    fee_str = format_rupiah(fee_val) if fee_val else "Rp0"
    lines.append(f"• *Biaya Admin* : `{fee_str}`")

    if warnings:
        lines.append("\n⚠️ *Peringatan Saldo:*")
        for w in warnings:
            lines.append(f"• {w}")

    context.user_data.pop("new_trf", None)
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def cancel_trf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Batalkan alur transfer."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Transfer dibatalkan.")
    context.user_data.pop("new_trf", None)
    return ConversationHandler.END


def get_transfer_conversation_handler() -> ConversationHandler:
    """Buat ConversationHandler untuk alur transfer antar metode pembayaran."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("transfer", start_add_transfer),
            MessageHandler(filters.Regex("^🔁 Transfer$"), start_add_transfer),
        ],
        states={
            STATE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_transfer_amount),
            ],
            STATE_FROM_METHOD: [
                CallbackQueryHandler(select_from_method_callback, pattern="^met_\\d+$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
            ],
            STATE_TO_METHOD: [
                CallbackQueryHandler(select_to_method_callback, pattern="^met_\\d+$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
            ],
            STATE_ADMIN_FEE: [
                CallbackQueryHandler(fee_zero_callback, pattern="^fee_zero$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_admin_fee_text),
            ],
            STATE_DATE: [
                CallbackQueryHandler(date_today_trf_callback, pattern="^date_today$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_trf_date_text),
            ],
            STATE_NOTE: [
                CallbackQueryHandler(note_skip_trf_callback, pattern="^note_skip$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_trf_note_text),
            ],
            STATE_CONFIRM: [
                CallbackQueryHandler(confirm_trf_save_callback, pattern="^confirm_trf_save$"),
                CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
            ],
        },
        fallbacks=[
            CommandHandler("batal", cancel_trf_callback),
            CommandHandler("cancel", cancel_trf_callback),
            CallbackQueryHandler(cancel_trf_callback, pattern="^cancel_action$"),
        ],
        allow_reentry=True,
    )
