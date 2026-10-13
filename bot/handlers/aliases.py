"""Handler untuk fitur input cepat Telegram dan manajemen alias (V2)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.auth import whitelist_required
from bot.keyboards import quick_confirmation_keyboard
from database.engine import session_scope
from database.queries import list_categories, list_methods
from models import TARGET_CATEGORY, TARGET_PAYMENT_METHOD, TYPE_EXPENSE, QuickAlias
from services import aliases, transactions
from services.aliases import ParsedQuickTransaction
from utils.errors import NotFoundError, ValidationError
from utils.formatting import format_rupiah

logger = logging.getLogger(__name__)


@whitelist_required
async def cmd_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan daftar seluruh alias input cepat milik pengguna."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_aliases() -> tuple[list[QuickAlias], dict[int, str], dict[int, str]]:
        with session_scope(factory) as session:
            user_aliases = aliases.list_aliases(session, user_id)
            cats = {c.id: c.name for c in list_categories(session, user_id)}
            mets = {m.id: m.name for m in list_methods(session, user_id)}
            return user_aliases, cats, mets

    user_aliases, cats, mets = await asyncio.to_thread(_get_aliases)

    if not user_aliases:
        msg = (
            "🏷️ *DAFTAR ALIAS INPUT CEPAT*\n\n"
            "Belum ada alias yang terdaftar.\n\n"
            "💡 *Cara menambah alias:*\n"
            "`/tambah_alias <alias> <kategori/metode>`\n"
            "Contoh: `/tambah_alias warteg Makanan` atau `/tambah_alias ovo E-Wallet`"
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    cat_aliases = [a for a in user_aliases if a.target_type == TARGET_CATEGORY]
    met_aliases = [a for a in user_aliases if a.target_type == TARGET_PAYMENT_METHOD]

    lines = ["🏷️ *DAFTAR ALIAS INPUT CEPAT*\n"]

    lines.append("*📂 Alias Kategori:*")
    if cat_aliases:
        for a in cat_aliases:
            cat_name = cats.get(a.category_id, f"ID #{a.category_id}")
            lines.append(f"• `{a.alias}` -> *{cat_name}*")
    else:
        lines.append("_(Belum ada alias kategori)_")

    lines.append("\n*💳 Alias Metode Pembayaran:*")
    if met_aliases:
        for a in met_aliases:
            met_name = mets.get(a.method_id, f"ID #{a.method_id}")
            lines.append(f"• `{a.alias}` -> *{met_name}*")
    else:
        lines.append("_(Belum ada alias metode)_")

    lines.append("\n―" * 20)
    lines.append(
        "💡 *Kelola Alias:*\n"
        "• Tambah: `/tambah_alias <alias> <kategori/metode>`\n"
        "• Hapus: `/hapus_alias <alias>`"
    )

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_tambah_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tambah alias baru untuk kategori atau metode pembayaran."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    args = context.args or []
    if len(args) < 2:
        msg = (
            "💡 *Format Perintah Tambah Alias:*\n"
            "`/tambah_alias <alias> <nama kategori / metode>`\n\n"
            "Contoh:\n"
            "• `/tambah_alias warteg Makanan`\n"
            "• `/tambah_alias ovo E-Wallet`\n"
            "• `/tambah_alias bensin Transportasi`"
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    alias_name = args[0].strip().lower()
    target_name = " ".join(args[1:]).strip().lower()

    def _save() -> tuple[str, str]:
        with session_scope(factory) as session:
            # Cari di kategori pengguna
            cats = [c for c in list_categories(session, user_id) if not c.is_archived]
            match_cat = next((c for c in cats if c.name.lower() == target_name), None)

            # Cari di metode pembayaran pengguna
            mets = [m for m in list_methods(session, user_id) if not m.is_archived]
            match_met = next((m for m in mets if m.name.lower() == target_name), None)

            if match_cat and not match_met:
                aliases.add_alias(session, user_id, alias_name, TARGET_CATEGORY, match_cat.id)
                session.commit()
                return "Kategori", match_cat.name
            elif match_met and not match_cat:
                aliases.add_alias(session, user_id, alias_name, TARGET_PAYMENT_METHOD, match_met.id)
                session.commit()
                return "Metode Pembayaran", match_met.name
            elif match_cat and match_met:
                # Ambigu jika nama persis sama, prioritaskan kategori
                aliases.add_alias(session, user_id, alias_name, TARGET_CATEGORY, match_cat.id)
                session.commit()
                return "Kategori", match_cat.name
            else:
                raise NotFoundError(
                    f"Target '{target_name}' tidak ditemukan pada "
                    "kategori atau metode pembayaran aktif."
                )

    try:
        target_kind, actual_target_name = await asyncio.to_thread(_save)
        await update.effective_message.reply_text(
            f"✅ Alias `{alias_name}` berhasil didaftarkan untuk "
            f"*{target_kind}: {actual_target_name}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except (ValidationError, NotFoundError) as e:
        await update.effective_message.reply_text(
            f"❌ *Gagal Menambah Alias:*\n{e}",
            parse_mode=ParseMode.MARKDOWN,
        )


@whitelist_required
async def cmd_hapus_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hapus alias input cepat."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    args = context.args or []
    if not args:
        msg = (
            "💡 *Format Perintah Hapus Alias:*\n"
            "`/hapus_alias <alias>`\n\n"
            "Contoh: `/hapus_alias warteg`"
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    alias_name = args[0].strip().lower()

    def _delete() -> None:
        with session_scope(factory) as session:
            user_aliases = aliases.list_aliases(session, user_id)
            match_qa = next((a for a in user_aliases if a.alias.lower() == alias_name), None)
            if not match_qa:
                raise NotFoundError(f"Alias '{alias_name}' tidak ditemukan.")
            aliases.delete_alias(session, user_id, match_qa.id)
            session.commit()

    try:
        await asyncio.to_thread(_delete)
        await update.effective_message.reply_text(
            f"✅ Alias `{alias_name}` berhasil dihapus.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except (ValidationError, NotFoundError) as e:
        await update.effective_message.reply_text(
            f"❌ *Gagal Menghapus Alias:*\n{e}",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Parse teks input cepat dan tanyakan konfirmasi ke pengguna."""
    if not update.effective_message:
        return False
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _parse() -> ParsedQuickTransaction:
        with session_scope(factory) as session:
            return aliases.parse_quick_input(session, user_id, text)

    try:
        parsed = await asyncio.to_thread(_parse)
    except ValidationError as e:
        # Jika teks tampaknya bukan format transaksi cepat, beri pesan panduan
        await update.effective_message.reply_text(
            f"⚠️ *Format Input Cepat Tidak Dikenali:*\n{e}\n\n"
            "💡 *Contoh Format:*\n"
            "• `makan 15000 gopay`\n"
            "• `bensin 25k cash untuk motor`\n"
            "• `1.5jt gaji bca`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return False

    context.user_data["pending_quick_tx"] = {
        "type": parsed.type,
        "amount": parsed.amount,
        "category_id": parsed.category_id,
        "category_name": parsed.category_name,
        "method_id": parsed.method_id,
        "method_name": parsed.method_name,
        "note": parsed.note,
    }

    type_label = "🔴 Pengeluaran" if parsed.type == TYPE_EXPENSE else "🟢 Pemasukan"
    lines = [
        "⚡ *KONFIRMASI INPUT CEPAT*\n",
        f"• *Jenis:* {type_label}",
        f"• *Nominal:* `{format_rupiah(parsed.amount)}`",
        f"• *Kategori:* {parsed.category_name}",
        f"• *Metode:* {parsed.method_name}",
        f"• *Catatan:* {parsed.note or '-'}",
        "\nSimpan transaksi ini?",
    ]

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=quick_confirmation_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return True


@whitelist_required
async def quick_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback konfirmasi simpan transaksi input cepat."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    pending = context.user_data.pop("pending_quick_tx", None)
    if not pending:
        await query.edit_message_text("⚠️ Sesi input cepat telah kedaluwarsa atau sudah diproses.")
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _execute() -> tuple[Any, list[str]]:
        with session_scope(factory) as session:
            tx_type = pending["type"]
            amount = pending["amount"]
            category_id = pending["category_id"]
            method_id = pending["method_id"]
            note = pending["note"]

            if tx_type == TYPE_EXPENSE:
                res = transactions.add_expense(
                    session, user_id, amount, category_id, method_id, on=None, note=note
                )
            else:
                res = transactions.add_income(
                    session, user_id, amount, category_id, method_id, on=None, note=note
                )
            session.commit()
            warnings = [w.message for w in res.warnings]
            return res.transaction, warnings

    try:
        tx, warnings = await asyncio.to_thread(_execute)
        type_str = "Pengeluaran" if tx.type == TYPE_EXPENSE else "Pemasukan"
        lines = [
            "✅ *TRANSAKSI BERHASIL DISIMPAN!*",
            f"• *ID:* `#{tx.id}`",
            f"• *Jenis:* {type_str}",
            f"• *Nominal:* `{format_rupiah(tx.amount)}`",
            f"• *Kategori:* {pending['category_name']}",
            f"• *Metode:* {pending['method_name']}",
        ]
        if pending.get("note"):
            lines.append(f"• *Catatan:* {pending['note']}")

        if warnings:
            lines.append("\n⚠️ *Peringatan:*")
            for w in warnings:
                lines.append(f"• {w}")

        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("Gagal menyimpan transaksi input cepat: %s", e, exc_info=e)
        await query.edit_message_text(f"❌ Terjadi kesalahan saat menyimpan transaksi: {e}")


@whitelist_required
async def quick_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback batalkan transaksi input cepat."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    context.user_data.pop("pending_quick_tx", None)
    await query.edit_message_text("❌ Input cepat dibatalkan.")
