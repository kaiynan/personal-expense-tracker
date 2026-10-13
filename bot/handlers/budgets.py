"""Handler status anggaran (/budget, /budget_mingguan, /rollover_budget) dan pengaturan budget."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
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

from bot.auth import whitelist_required
from bot.keyboards import budget_action_keyboard, categories_inline_keyboard
from database.engine import session_scope
from models import TYPE_EXPENSE
from services import budgets, categories
from utils.dates import format_display
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import validate_amount

logger = logging.getLogger(__name__)

# State percakapan atur budget
BGT_STATE_CHOICE, BGT_STATE_CATEGORY, BGT_STATE_AMOUNT = range(3)


def _status_icon(level: str) -> str:
    """Kembalikan emoji status evaluasi budget."""
    if level == "exceeded":
        return "🔴"
    if level == "warning":
        return "🟡"
    return "🟢"


@whitelist_required
async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan status evaluasi anggaran bulan ini & minggu ini beserta tombol aksi."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_budget_status() -> tuple[
        budgets.MonthlyBudgetEvaluation, budgets.WeeklyBudgetEvaluation
    ]:
        with session_scope(factory) as session:
            return (
                budgets.evaluate_budgets(session, user_id),
                budgets.evaluate_weekly_budgets(session, user_id),
            )

    eval_res, w_eval = await asyncio.to_thread(_get_budget_status)

    lines = [f"🎯 *STATUS ANGGARAN (BUDGET)* ({eval_res.month})\n"]
    if eval_res.total_budget:
        tb = eval_res.total_budget
        status_icon = _status_icon(tb.warning_level)
        lines.append("*Budget Total Bulanan:*")
        lines.append(f"• Alokasi    : `{format_rupiah(tb.amount)}`")
        if tb.carryover_amount > 0:
            lines.append(
                f"  _(Dasar `{format_rupiah(tb.base_amount)}` + "
                f"Rollover `{format_rupiah(tb.carryover_amount)}`)_"
            )
        lines.append(f"• Terpakai   : `{format_rupiah(tb.spent)}` ({tb.percentage:.1f}%)")
        lines.append(f"• Sisa       : `{format_rupiah(tb.remaining)}`")
        lines.append(f"• Status     : {status_icon} *{tb.warning_level.upper()}*\n")
    else:
        lines.append("*Budget Total Bulanan*: _(Belum diatur untuk bulan ini)_\n")

    if eval_res.category_budgets:
        lines.append("*Budget Bulanan per Kategori:*")
        for cb in eval_res.category_budgets:
            icon = _status_icon(cb.warning_level)
            ro_text = (
                f" [+{format_rupiah(cb.carryover_amount)} rollover]"
                if cb.carryover_amount > 0
                else ""
            )
            lines.append(
                f"• {icon} *{cb.category_name}*: `{format_rupiah(cb.spent)}` / "
                f"`{format_rupiah(cb.amount)}`{ro_text} ({cb.percentage:.1f}%)"
            )
        lines.append("")

    # Tampilkan ringkasan budget mingguan jika ada yang diatur
    w_range_str = f"{format_display(w_eval.week_start)} s.d. {format_display(w_eval.week_end)}"
    if w_eval.total_budget or w_eval.category_budgets:
        lines.append(f"📅 *Budget Mingguan* ({w_range_str}):")
        if w_eval.total_budget:
            wtb = w_eval.total_budget
            w_icon = _status_icon(wtb.warning_level)
            lines.append(
                f"• {w_icon} *Total Mingguan*: `{format_rupiah(wtb.spent)}` / "
                f"`{format_rupiah(wtb.amount)}` ({wtb.percentage:.1f}%)"
            )
        for wcb in w_eval.category_budgets:
            w_icon = _status_icon(wcb.warning_level)
            lines.append(
                f"• {w_icon} *{wcb.category_name}*: `{format_rupiah(wcb.spent)}` / "
                f"`{format_rupiah(wcb.amount)}` ({wcb.percentage:.1f}%)"
            )
        lines.append("")

    all_warnings = [*eval_res.warning_messages, *w_eval.warning_messages]
    if all_warnings:
        lines.append("⚠️ *Peringatan Batas Anggaran:*")
        for msg in all_warnings:
            lines.append(f"• {msg}")
        lines.append("")

    lines.append("Tekan tombol di bawah jika ingin mengatur anggaran:")

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=budget_action_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_required
async def cmd_budget_mingguan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan status evaluasi anggaran mingguan (Senin s.d. Minggu)."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_weekly() -> budgets.WeeklyBudgetEvaluation:
        with session_scope(factory) as session:
            return budgets.evaluate_weekly_budgets(session, user_id)

    w_eval = await asyncio.to_thread(_get_weekly)
    w_range_str = f"{format_display(w_eval.week_start)} s.d. {format_display(w_eval.week_end)}"
    lines = [f"📅 *STATUS BUDGET MINGGUAN*\nPeriode: `{w_range_str}`\n"]

    if w_eval.total_budget:
        tb = w_eval.total_budget
        icon = _status_icon(tb.warning_level)
        lines.append("*Budget Total Mingguan:*")
        lines.append(f"• Alokasi  : `{format_rupiah(tb.amount)}`")
        lines.append(f"• Terpakai : `{format_rupiah(tb.spent)}` ({tb.percentage:.1f}%)")
        lines.append(f"• Sisa     : `{format_rupiah(tb.remaining)}`")
        lines.append(f"• Status   : {icon} *{tb.warning_level.upper()}*\n")
    else:
        lines.append("*Budget Total Mingguan*: _(Belum diatur untuk minggu ini)_\n")

    if w_eval.category_budgets:
        lines.append("*Budget Mingguan per Kategori:*")
        for cb in w_eval.category_budgets:
            icon = _status_icon(cb.warning_level)
            lines.append(
                f"• {icon} *{cb.category_name}*: `{format_rupiah(cb.spent)}` / "
                f"`{format_rupiah(cb.amount)}` ({cb.percentage:.1f}%)"
            )
        lines.append("")

    if w_eval.has_warnings:
        lines.append("⚠️ *Peringatan Batas Mingguan:*")
        for msg in w_eval.warning_messages:
            lines.append(f"• {msg}")

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=budget_action_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_required
async def cmd_rollover_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bawa sisa budget bulan lalu ke bulan ini (/rollover_budget [YYYY-MM] [YYYY-MM])."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    args = context.args or []
    from_m = args[0] if len(args) >= 1 else None
    to_m = args[1] if len(args) >= 2 else None

    def _do_rollover() -> list[budgets.RolloverResultItem]:
        with session_scope(factory) as session:
            return budgets.rollover_budgets(session, user_id, from_month=from_m, to_month=to_m)

    try:
        rolled = await asyncio.to_thread(_do_rollover)
    except ValidationError as err:
        await update.effective_message.reply_text(f"❌ Gagal rollover budget: {err}")
        return

    if not rolled:
        await update.effective_message.reply_text(
            "💡 Tidak ada budget pada bulan sebelumnya untuk di-rollover."
        )
        return

    lines = [f"🔄 *ROLLOVER BUDGET BERHASIL* (`{rolled[0].from_month}` ➡️ `{rolled[0].to_month}`)\n"]
    for item in rolled:
        lines.append(
            f"• *{item.category_name}*:\n"
            f"  Sisa dibawa: `{format_rupiah(item.surplus_carried_over)}`\n"
            f"  Budget baru: `{format_rupiah(item.new_effective_budget)}` "
            f"_(Dasar `{format_rupiah(item.new_base_amount)}` + "
            f"Rollover `{format_rupiah(item.surplus_carried_over)}`)_"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_required
async def budget_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Proses pilihan pengaturan budget bulanan, mingguan, atau rollover."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    data = query.data
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    context.user_data["budget_setup"] = {}

    if data == "bgt_rollover":

        def _do_rollover() -> list[budgets.RolloverResultItem]:
            with session_scope(factory) as session:
                return budgets.rollover_budgets(session, user_id)

        try:
            rolled = await asyncio.to_thread(_do_rollover)
        except ValidationError as err:
            await query.edit_message_text(f"❌ Gagal rollover budget: {err}")
            return ConversationHandler.END

        if not rolled:
            await query.edit_message_text(
                "💡 Tidak ada budget pada bulan lalu untuk dibawa ke bulan ini."
            )
            return ConversationHandler.END

        lines = [
            f"🔄 *ROLLOVER BUDGET BERHASIL* (`{rolled[0].from_month}` ➡️ `{rolled[0].to_month}`)\n"
        ]
        for item in rolled:
            surplus_str = format_rupiah(item.surplus_carried_over)
            new_tot_str = format_rupiah(item.new_effective_budget)
            lines.append(
                f"• *{item.category_name}*: Sisa dibawa `{surplus_str}` "
                f"➡️ Total Baru `{new_tot_str}`"
            )
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    if data == "bgt_set_total":
        context.user_data["budget_setup"]["target"] = "total"
        await query.edit_message_text(
            "Masukkan nominal budget total bulanan dalam rupiah (contoh: `2500000`):"
        )
        return BGT_STATE_AMOUNT

    if data == "bgt_set_weekly_total":
        context.user_data["budget_setup"]["target"] = "weekly_total"
        await query.edit_message_text(
            "Masukkan nominal budget total mingguan dalam rupiah (contoh: `750000`):"
        )
        return BGT_STATE_AMOUNT

    if data in ("bgt_set_category", "bgt_set_weekly_category"):
        is_weekly = data == "bgt_set_weekly_category"
        context.user_data["budget_setup"]["target"] = "weekly_category" if is_weekly else "category"

        def _get_cats() -> list[Any]:
            with session_scope(factory) as session:
                return categories.list_categories(session, user_id, type_=TYPE_EXPENSE)

        cats = await asyncio.to_thread(_get_cats)
        if not cats:
            await query.edit_message_text("❌ Tidak ada kategori pengeluaran aktif.")
            return ConversationHandler.END

        period_text = "mingguan" if is_weekly else "bulanan"
        await query.edit_message_text(
            f"Pilih kategori pengeluaran yang ingin diatur anggaran {period_text}nya:",
            reply_markup=categories_inline_keyboard(cats),
        )
        return BGT_STATE_CATEGORY

    return ConversationHandler.END


async def budget_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Simpan kategori yang dipilih untuk budget."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    cat_id = int(query.data.removeprefix("cat_"))
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_cat_name() -> str:
        with session_scope(factory) as session:
            c = categories.get_category(session, user_id, cat_id)
            return c.name if c else "Kategori"

    cat_name = await asyncio.to_thread(_get_cat_name)
    context.user_data["budget_setup"]["category_id"] = cat_id
    context.user_data["budget_setup"]["category_name"] = cat_name

    await query.edit_message_text(
        f"Kategori: *{cat_name}*\n\n"
        "Masukkan nominal anggaran untuk kategori ini dalam rupiah (contoh: `500000`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return BGT_STATE_AMOUNT


async def enter_budget_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validasi nominal budget dan simpan ke database."""
    if not update.effective_message or not update.effective_message.text:
        return BGT_STATE_AMOUNT

    text = update.effective_message.text.strip()
    try:
        amount = validate_amount(text)
    except ValidationError as err:
        await update.effective_message.reply_text(
            f"❌ Nominal tidak valid: {err}\nSilakan masukkan nominal angka positif:"
        )
        return BGT_STATE_AMOUNT

    bgt_info = context.user_data.get("budget_setup", {})
    target = bgt_info.get("target")
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _save() -> tuple[str, str]:
        with session_scope(factory) as session:
            if target == "total":
                b = budgets.set_monthly_budget(session, user_id, amount=amount)
                return "Total Bulanan", f"bulan {b.month}"
            if target == "weekly_total":
                wb = budgets.set_weekly_budget(session, user_id, amount=amount)
                w_end = wb.week_start + timedelta(days=6)
                return (
                    "Total Mingguan",
                    f"minggu {format_display(wb.week_start)} s.d. {format_display(w_end)}",
                )
            cat_id = bgt_info["category_id"]
            cat_name = bgt_info["category_name"]
            if target == "weekly_category":
                wb = budgets.set_weekly_budget(session, user_id, amount=amount, category_id=cat_id)
                w_end = wb.week_start + timedelta(days=6)
                return (
                    f"Mingguan Kategori '{cat_name}'",
                    f"minggu {format_display(wb.week_start)} s.d. {format_display(w_end)}",
                )
            b = budgets.set_category_budget(session, user_id, category_id=cat_id, amount=amount)
            return f"Kategori '{cat_name}'", f"bulan {b.month}"

    try:
        label, period_str = await asyncio.to_thread(_save)
        logger.info("Budget berhasil diatur untuk user_id=%d, target=%s", user_id, target)
        bgt_text = format_rupiah(amount)
        await update.effective_message.reply_text(
            f"✅ Budget {label} {period_str} berhasil diatur sebesar *{bgt_text}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValidationError as err:
        await update.effective_message.reply_text(f"❌ Gagal menyimpan budget: {err}")

    context.user_data.pop("budget_setup", None)
    return ConversationHandler.END


async def cancel_budget_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Batalkan alur budget."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Pengaturan budget dibatalkan.")
    context.user_data.pop("budget_setup", None)
    return ConversationHandler.END


def get_budget_conversation_handler() -> ConversationHandler:
    """ConversationHandler untuk mengatur budget bulanan, mingguan, maupun rollover."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                budget_choice_callback,
                pattern="^bgt_(set_total|set_category|set_weekly_total|set_weekly_category|rollover)$",
            ),
        ],
        states={
            BGT_STATE_CATEGORY: [
                CallbackQueryHandler(budget_category_callback, pattern="^cat_\\d+$"),
                CallbackQueryHandler(cancel_budget_callback, pattern="^cancel_action$"),
            ],
            BGT_STATE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_budget_amount),
            ],
        },
        fallbacks=[
            CommandHandler("batal", cancel_budget_callback),
            CommandHandler("cancel", cancel_budget_callback),
            CallbackQueryHandler(cancel_budget_callback, pattern="^cancel_action$"),
        ],
        allow_reentry=True,
    )
