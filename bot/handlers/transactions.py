"""Percakapan bertahap untuk mencatat transaksi (pemasukan & pengeluaran)."""

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
    categories_inline_keyboard,
    confirmation_keyboard,
    methods_inline_keyboard,
    skip_note_keyboard,
    today_date_keyboard,
    transaction_type_keyboard,
)
from database.engine import session_scope
from models import TYPE_EXPENSE, TYPE_INCOME
from services import balances, budgets, categories, transactions
from utils.dates import today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date, validate_amount, validate_note

logger = logging.getLogger(__name__)

# State percakapan bertahap
(
    STATE_TYPE,
    STATE_AMOUNT,
    STATE_CATEGORY,
    STATE_METHOD,
    STATE_DATE,
    STATE_NOTE,
    STATE_CONFIRM,
) = range(7)


@whitelist_required
async def start_add_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mulai percakapan pencatatan transaksi: pilih jenis pemasukan atau pengeluaran."""
    context.user_data["new_tx"] = {}
    msg = "Pilih jenis transaksi yang ingin dicatat:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=transaction_type_keyboard())
    elif update.effective_message:
        await update.effective_message.reply_text(msg, reply_markup=transaction_type_keyboard())
    return STATE_TYPE


async def select_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pilihan jenis transaksi (income / expense)."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    data = query.data
    tx_type = TYPE_INCOME if data == "type_income" else TYPE_EXPENSE
    context.user_data.setdefault("new_tx", {})["type"] = tx_type

    type_label = "Pemasukan" if tx_type == TYPE_INCOME else "Pengeluaran"
    await query.edit_message_text(
        f"Jenis: *{type_label}*\n\n"
        "Ketik nominal transaksi dalam rupiah (angka bulat positif, contoh: `50000`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_AMOUNT


async def enter_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi input nominal transaksi."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_AMOUNT

    text = update.effective_message.text.strip()
    try:
        amount = validate_amount(text)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Masukan tidak valid: {err}\nSilakan masukkan nominal berupa angka positif:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_AMOUNT

    context.user_data["new_tx"]["amount"] = amount
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    tx_type = context.user_data["new_tx"]["type"]

    def _get_cats() -> list[Any]:
        with session_scope(factory) as session:
            return categories.list_categories(session, user_id, type_=tx_type)

    cat_list = await asyncio.to_thread(_get_cats)
    if not cat_list:
        await update.effective_message.reply_text(
            "❌ Tidak ada kategori aktif untuk jenis transaksi ini. Transaksi dibatalkan."
        )
        context.user_data.pop("new_tx", None)
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"Nominal: *{format_rupiah(amount)}*\n\nPilih kategori transaksi:",
        reply_markup=categories_inline_keyboard(cat_list),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CATEGORY


async def select_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Simpan kategori yang dipilih lalu minta pilihan metode pembayaran."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    cat_id = int(query.data.removeprefix("cat_"))
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_cat_and_methods() -> tuple[str, list[Any]]:
        with session_scope(factory) as session:
            cat = categories.get_category(session, user_id, cat_id)
            cname = cat.name if cat else "Kategori"
            m_list = [
                m for m in balances.get_method_balances(session, user_id) if not m.is_archived
            ]
            return cname, m_list

    cat_name, method_list = await asyncio.to_thread(_get_cat_and_methods)
    context.user_data["new_tx"]["category_id"] = cat_id
    context.user_data["new_tx"]["category_name"] = cat_name

    if not method_list:
        await query.edit_message_text(
            "❌ Tidak ada metode pembayaran aktif yang tersedia. Transaksi dibatalkan."
        )
        context.user_data.pop("new_tx", None)
        return ConversationHandler.END

    await query.edit_message_text(
        f"Kategori: *{cat_name}*\n\nPilih metode pembayaran:",
        reply_markup=methods_inline_keyboard(method_list),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_METHOD


async def select_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Simpan metode pembayaran lalu minta tanggal transaksi."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    method_id = int(query.data.removeprefix("met_"))
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_method_name() -> str:
        with session_scope(factory) as session:
            for m in balances.get_method_balances(session, user_id):
                if m.method_id == method_id:
                    return m.name
            return "Metode"

    m_name = await asyncio.to_thread(_get_method_name)
    context.user_data["new_tx"]["method_id"] = method_id
    context.user_data["new_tx"]["method_name"] = m_name

    await query.edit_message_text(
        f"Metode: *{m_name}*\n\n"
        "Masukkan tanggal transaksi (format `DD-MM-YYYY`, contoh: `12-10-2026`) "
        "atau tekan tombol di bawah:",
        reply_markup=today_date_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_DATE


async def date_today_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gunakan tanggal hari ini menurut zona waktu pengguna."""
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
    context.user_data["new_tx"]["date"] = today_str

    await query.edit_message_text(
        f"Tanggal: *{today_str}*\n\nMasukkan keterangan transaksi (opsional) atau lewati:",
        reply_markup=skip_note_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_NOTE


async def enter_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi input teks tanggal transaksi."""
    if not update.effective_message or not update.effective_message.text:
        return STATE_DATE

    raw = update.effective_message.text.strip()
    try:
        _ = parse_date(raw)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Tanggal tidak valid: {err}\n"
            "Masukkan format `DD-MM-YYYY` (contoh: `12-10-2026`) atau gunakan tombol:",
            reply_markup=today_date_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_DATE

    context.user_data["new_tx"]["date"] = raw
    await update.effective_message.reply_text(
        f"Tanggal: *{raw}*\n\n"
        "Masukkan keterangan transaksi (opsional) atau tekan tombol untuk lewati:",
        reply_markup=skip_note_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_NOTE


async def note_skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Lewati pengisian keterangan transaksi."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    context.user_data["new_tx"]["note"] = ""
    tx_data = context.user_data["new_tx"]
    summary = _format_tx_summary(tx_data)
    await query.edit_message_text(
        summary,
        reply_markup=confirmation_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CONFIRM


async def enter_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi dan simpan keterangan transaksi."""
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

    context.user_data["new_tx"]["note"] = clean_note
    tx_data = context.user_data["new_tx"]
    summary = _format_tx_summary(tx_data)
    await update.effective_message.reply_text(
        summary,
        reply_markup=confirmation_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CONFIRM


def _format_tx_summary(tx: dict[str, Any]) -> str:
    type_name = "Pemasukan 🟢" if tx["type"] == TYPE_INCOME else "Pengeluaran 🔴"
    note_display = tx.get("note") or "_(tanpa keterangan)_"
    return (
        "🔍 *KONFIRMASI TRANSAKSI*\n\n"
        f"• *Jenis*      : {type_name}\n"
        f"• *Nominal*    : `{format_rupiah(tx['amount'])}`\n"
        f"• *Kategori*   : {tx['category_name']}\n"
        f"• *Metode*     : {tx['method_name']}\n"
        f"• *Tanggal*    : {tx['date']}\n"
        f"• *Keterangan* : {note_display}\n\n"
        "Apakah data transaksi di atas sudah benar?"
    )


async def confirm_save_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Eksekusi penyimpanan transaksi ke database dan laporkan warnings jika ada."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    tx_data = context.user_data.get("new_tx")
    if not tx_data:
        await query.edit_message_text("❌ Data transaksi kedaluwarsa.")
        return ConversationHandler.END

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    tx_type = tx_data["type"]
    amount = tx_data["amount"]
    cat_id = tx_data["category_id"]
    method_id = tx_data["method_id"]
    on_date = tx_data["date"]
    note = tx_data.get("note")

    def _save() -> tuple[int, list[str], list[str]]:
        with session_scope(factory) as session:
            if tx_type == TYPE_INCOME:
                res = transactions.add_income(
                    session, user_id, amount, cat_id, method_id, on=on_date, note=note
                )
            else:
                res = transactions.add_expense(
                    session, user_id, amount, cat_id, method_id, on=on_date, note=note
                )
            tx_id = res.transaction.id
            warns = list(res.warnings)
            # Evaluasi peringatan anggaran jika ada
            b_warns = (
                budgets.check_budget_warnings(session, user_id) if tx_type == TYPE_EXPENSE else []
            )
            return tx_id, warns, b_warns

    try:
        tx_id, warnings, budget_warnings = await asyncio.to_thread(_save)
        # Log keberhasilan tanpa mencatat data sensitif nominal/keterangan
        logger.info("Transaksi baru berhasil disimpan untuk user_id=%d, tx_id=%d", user_id, tx_id)
    except ValidationError as err:
        await query.edit_message_text(f"❌ Gagal menyimpan transaksi: {err}")
        context.user_data.pop("new_tx", None)
        return ConversationHandler.END

    lines = [f"✅ *Transaksi berhasil dicatat!* (`#{tx_id}`)\n"]
    type_label = "Pemasukan" if tx_type == TYPE_INCOME else "Pengeluaran"
    lines.append(f"• *Jenis*   : {type_label}")
    lines.append(f"• *Nominal* : `{format_rupiah(amount)}`")
    lines.append(f"• *Kategori*: {tx_data['category_name']}")
    lines.append(f"• *Metode*  : {tx_data['method_name']}")
    lines.append(f"• *Tanggal* : {on_date}")

    if warnings:
        lines.append("\n⚠️ *Peringatan Saldo:*")
        for w in warnings:
            lines.append(f"• {w}")

    if budget_warnings:
        lines.append("\n🎯 *Peringatan Anggaran:*")
        for bw in budget_warnings:
            lines.append(f"• {bw}")

    context.user_data.pop("new_tx", None)
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def cancel_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Batalkan alur transaksi."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Pencatatan transaksi dibatalkan.")
    context.user_data.pop("new_tx", None)
    return ConversationHandler.END


@whitelist_required
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Batalkan percakapan via perintah /batal."""
    context.user_data.pop("new_tx", None)
    if update.effective_message:
        await update.effective_message.reply_text("❌ Tindakan dibatalkan.")
    return ConversationHandler.END


def get_transaction_conversation_handler() -> ConversationHandler:
    """Buat ConversationHandler untuk pencatatan transaksi pemasukan/pengeluaran."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("tambah", start_add_transaction),
            MessageHandler(filters.Regex(r"^\+ Tambah Transaksi$"), start_add_transaction),
        ],
        states={
            STATE_TYPE: [
                CallbackQueryHandler(select_type_callback, pattern="^type_(income|expense)$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
            ],
            STATE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount),
            ],
            STATE_CATEGORY: [
                CallbackQueryHandler(select_category_callback, pattern="^cat_\\d+$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
            ],
            STATE_METHOD: [
                CallbackQueryHandler(select_method_callback, pattern="^met_\\d+$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
            ],
            STATE_DATE: [
                CallbackQueryHandler(date_today_callback, pattern="^date_today$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_date_text),
            ],
            STATE_NOTE: [
                CallbackQueryHandler(note_skip_callback, pattern="^note_skip$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_note_text),
            ],
            STATE_CONFIRM: [
                CallbackQueryHandler(confirm_save_callback, pattern="^confirm_save$"),
                CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
            ],
        },
        fallbacks=[
            CommandHandler("batal", cancel_command),
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_action_callback, pattern="^cancel_action$"),
        ],
        allow_reentry=True,
    )
