"""Handler perintah cepat: /saldo, /hari_ini, dan /riwayat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import whitelist_required
from database import queries
from database.engine import session_scope
from models import TYPE_ADJUSTMENT, TYPE_EXPENSE, TYPE_INCOME, TYPE_TRANSFER
from services import audit, balances, categories, transactions
from utils.dates import format_display, today
from utils.errors import NotFoundError, ValidationError
from utils.formatting import format_rupiah

logger = logging.getLogger(__name__)


@whitelist_required
async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan saldo saat ini untuk setiap metode pembayaran aktif dan total saldo."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_data() -> tuple[list[Any], int]:
        with session_scope(factory) as session:
            all_m = balances.get_method_balances(session, user_id)
            tot = balances.get_total_balance(session, user_id)
            return all_m, tot

    method_balances, total_balance = await asyncio.to_thread(_get_data)

    active_methods = [m for m in method_balances if not m.is_archived]
    lines = ["💰 *SALDO SAAT INI*\n"]
    if active_methods:
        for m in active_methods:
            lines.append(f"• *{m.name}*: `{format_rupiah(m.balance)}`")
    else:
        lines.append("_(Belum ada metode pembayaran aktif)_")

    lines.append("―" * 20)
    lines.append(f"*TOTAL SALDO*: `{format_rupiah(total_balance)}`")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_hari_ini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan ringkasan dan daftar transaksi hari ini."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_today_data() -> tuple[Any, list[Any], dict[int, str], dict[int, str]]:
        with session_scope(factory) as session:
            user = queries.get_user(session, user_id)
            tz_name = user.timezone if user else config.TIMEZONE_NAME
            today_d = today(tz_name)
            tx_list = transactions.list_transactions(
                session, user_id, start=today_d, end=today_d, limit=50
            )
            cats = categories.list_categories(
                session, user_id, include_archived=True, include_system=True
            )
            meths = balances.get_method_balances(session, user_id)
            cat_map = {c.id: c.name for c in cats}
            meth_map = {m.method_id: m.name for m in meths}
            return today_d, tx_list, cat_map, meth_map

    today_date, tx_list, cat_map, meth_map = await asyncio.to_thread(_get_today_data)

    total_inc = sum(t.amount for t in tx_list if t.type == TYPE_INCOME)
    total_exp = sum(t.amount for t in tx_list if t.type == TYPE_EXPENSE)

    date_str = format_display(today_date)
    lines = [f"📅 *TRANSAKSI HARI INI* ({date_str})\n"]
    lines.append(f"• Total Pemasukan   : `{format_rupiah(total_inc)}`")
    lines.append(f"• Total Pengeluaran : `{format_rupiah(total_exp)}`")
    lines.append(f"• Jumlah Transaksi  : {len(tx_list)} transaksi\n")

    if not tx_list:
        lines.append("_(Belum ada transaksi yang dicatat hari ini)_")
    else:
        lines.append("*Rincian Transaksi:*")
        for t in tx_list:
            m_name = meth_map.get(t.method_id, "Metode")
            if t.type == TYPE_INCOME:
                c_name = cat_map.get(t.category_id, "Pemasukan")
                lines.append(f"• `[+{format_rupiah(t.amount)}]` 🟢 {c_name} via {m_name}")
            elif t.type == TYPE_EXPENSE:
                c_name = cat_map.get(t.category_id, "Pengeluaran")
                lines.append(f"• `[-{format_rupiah(t.amount)}]` 🔴 {c_name} via {m_name}")
            elif t.type == TYPE_TRANSFER:
                to_m = meth_map.get(t.to_method_id, "Tujuan")
                lines.append(f"• `[{format_rupiah(t.amount)}]` 🔁 Transfer {m_name} ➔ {to_m}")
            elif t.type == TYPE_ADJUSTMENT:
                sign = "+" if t.amount > 0 else ""
                lines.append(f"• `[{sign}{format_rupiah(t.amount)}]` ⚖️ Penyesuaian {m_name}")
            if t.note:
                lines.append(f"   _{t.note}_")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan 10 transaksi terakhir."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_history() -> tuple[list[Any], dict[int, str], dict[int, str]]:
        with session_scope(factory) as session:
            tx_list = transactions.list_transactions(session, user_id, limit=10)
            cats = categories.list_categories(
                session, user_id, include_archived=True, include_system=True
            )
            meths = balances.get_method_balances(session, user_id)
            cat_map = {c.id: c.name for c in cats}
            meth_map = {m.method_id: m.name for m in meths}
            return tx_list, cat_map, meth_map

    tx_list, cat_map, meth_map = await asyncio.to_thread(_get_history)

    lines = ["📜 *10 TRANSAKSI TERAKHIR*\n"]
    if not tx_list:
        lines.append("_(Belum ada transaksi tersimpan)_")
    else:
        for t in tx_list:
            d_str = format_display(t.date)
            m_name = meth_map.get(t.method_id, "Metode")
            if t.type == TYPE_INCOME:
                c_name = cat_map.get(t.category_id, "Pemasukan")
                tag = f"🟢 +{format_rupiah(t.amount)}"
                detail = f"{c_name} ({m_name})"
            elif t.type == TYPE_EXPENSE:
                c_name = cat_map.get(t.category_id, "Pengeluaran")
                tag = f"🔴 -{format_rupiah(t.amount)}"
                detail = f"{c_name} ({m_name})"
            elif t.type == TYPE_TRANSFER:
                to_m = meth_map.get(t.to_method_id, "Tujuan")
                tag = f"🔁 {format_rupiah(t.amount)}"
                detail = f"{m_name} ➔ {to_m}"
            else:
                tag = f"⚖️ {format_rupiah(t.amount)}"
                detail = f"Koreksi {m_name}"

            note_str = f" - _{t.note}_" if t.note else ""
            lines.append(f"• `#{t.id}` [{d_str}] *{tag}*\n   {detail}{note_str}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_konsistensi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cek konsistensi saldo antara compute_balance dan perhitungan data mentah."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _check() -> list[Any]:
        with session_scope(factory) as session:
            return balances.check_balance_consistency(session, user_id)

    results = await asyncio.to_thread(_check)
    lines = ["⚖️ *CEK KONSISTENSI SALDO*\n"]
    lines.append("Perbandingan hasil `compute_balance` (agregat) vs data mentah:\n")
    all_ok = True
    for r in results:
        status_icon = "✅" if r.is_consistent else "❌"
        if not r.is_consistent:
            all_ok = False
        lines.append(f"• *{r.name}*: {status_icon}")
        lines.append(f"   Agregat: `{format_rupiah(r.compute_balance)}`")
        lines.append(f"   Data Mentah: `{format_rupiah(r.raw_balance)}`")
        if not r.is_consistent:
            lines.append(f"   _Selisih: {format_rupiah(r.difference)}_")

    lines.append("\n" + ("―" * 20))
    if all_ok:
        lines.append("✨ *Hasil: SEMUA SALDO KONSISTEN!*")
    else:
        lines.append("⚠️ *Hasil: DITEMUKAN SELISIH SALDO!*")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Batalkan (undo) aksi transaksi terakhir."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _do_undo() -> tuple[str, list[str]]:
        with session_scope(factory) as session:
            try:
                res = audit.undo_last(session, user_id)
                msg = f"✅ Berhasil membatalkan aksi: {res.audit_log.description}"
                warns = [w.message for w in res.warnings]
                return msg, warns
            except (ValidationError, NotFoundError) as err:
                return f"❌ {err}", []

    message, warnings = await asyncio.to_thread(_do_undo)
    if warnings:
        warn_text = "\n\n⚠️ *Peringatan:*\n" + "\n".join(f"• {w}" for w in warnings)
        message += warn_text
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_riwayat_perubahan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan riwayat perubahan / audit log terakhir."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_logs() -> list[Any]:
        with session_scope(factory) as session:
            return audit.list_audit_logs(session, user_id, limit=10)

    logs = await asyncio.to_thread(_get_logs)
    if not logs:
        await update.effective_message.reply_text("[*] Belum ada riwayat perubahan yang tercatat.")
        return

    lines = ["📜 *RIWAYAT PERUBAHAN (10 Terakhir)*\n"]
    for entry in logs:
        status_tag = " _[SUDAH DI-UNDO]_" if entry.is_undone else ""
        time_str = entry.created_at.strftime("%d-%m-%Y %H:%M")
        desc = entry.description or "-"
        lines.append(f"• `#{entry.id}` [{entry.action.upper()}] {desc} ({time_str}){status_tag}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
