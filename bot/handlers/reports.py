"""Handler laporan keuangan (/laporan) dan grafik (/grafik)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import whitelist_required
from bot.keyboards import reports_menu_keyboard
from charts import generate_all_charts
from database.engine import session_scope
from reports.financial import (
    FinancialReport,
    FinancialStatistics,
    PeriodComparisonReport,
    get_daily_report,
    get_financial_statistics,
    get_monthly_comparison,
    get_monthly_report,
    get_weekly_report,
)
from utils.dates import format_display, today
from utils.formatting import format_rupiah

logger = logging.getLogger(__name__)


@whitelist_required
async def cmd_laporan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan pilihan periode laporan keuangan."""
    if not update.effective_message:
        return
    msg = "📊 *LAPORAN KEUANGAN*\n\nPilih periode laporan yang ingin dilihat:"
    await update.effective_message.reply_text(
        msg, reply_markup=reports_menu_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback pemrosesan pilihan laporan keuangan di Telegram."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    rep_type = query.data

    if rep_type == "rep_compare":

        def _get_comparison() -> PeriodComparisonReport:
            with session_scope(factory) as session:
                return get_monthly_comparison(session, user_id)

        rep_comp = await asyncio.to_thread(_get_comparison)
        text = _format_bot_comparison(rep_comp)
    elif rep_type == "rep_stats":

        def _get_stats() -> FinancialStatistics:
            with session_scope(factory) as session:
                return get_financial_statistics(session, user_id)

        stats = await asyncio.to_thread(_get_stats)
        text = _format_bot_statistics(stats)
    else:

        def _get_report() -> tuple[str, FinancialReport]:
            with session_scope(factory) as session:
                if rep_type == "rep_daily":
                    r = get_daily_report(session, user_id)
                    label = "HARIAN"
                elif rep_type == "rep_weekly":
                    r = get_weekly_report(session, user_id)
                    label = "MINGGUAN"
                else:
                    r = get_monthly_report(session, user_id)
                    label = "BULANAN"
                return label, r

        label, rep = await asyncio.to_thread(_get_report)
        text = _format_bot_report(label, rep)

    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


def _format_bot_report(label: str, rep: FinancialReport) -> str:
    start_str = format_display(rep.start_date)
    end_str = format_display(rep.end_date)
    net_sign = "+" if rep.net > 0 else ""

    date_header = (
        f"Tanggal: `{start_str}`\n"
        if rep.start_date == rep.end_date
        else f"Periode: `{start_str}` s.d. `{end_str}`\n"
    )

    lines = [
        f"📊 *LAPORAN KEUANGAN {label}*",
        date_header,
        f"• Total Pemasukan   : `{format_rupiah(rep.total_income)}`",
        f"• Total Pengeluaran : `{format_rupiah(rep.total_expense)}`",
        f"• Arus Kas Bersih   : `{net_sign}{format_rupiah(rep.net)}`",
        f"• Jumlah Transaksi  : {rep.transaction_count} transaksi\n",
    ]

    if rep.category_expenses:
        lines.append("*Pengeluaran per Kategori:*")
        for c in rep.category_expenses:
            lines.append(f"• {c.category_name}: `{format_rupiah(c.amount)}` ({c.percentage:.1f}%)")
        lines.append("")

    if rep.top_expenses:
        lines.append("*Pengeluaran Terbesar:*")
        for te in rep.top_expenses:
            d_str = format_display(te.date)
            note_str = f" - _{te.note}_" if te.note else ""
            lines.append(f"• `{format_rupiah(te.amount)}` ({te.category_name}) [{d_str}]{note_str}")

    return "\n".join(lines)


def _format_bot_comparison(rep: PeriodComparisonReport) -> str:
    p1, p2 = rep.period1, rep.period2

    def _diff_str(diff: int, pct: float | None) -> str:
        sign = "+" if diff > 0 else "-" if diff < 0 else ""
        pct_str = f" ({sign}{abs(pct):.1f}%)" if pct is not None else ""
        return f"`{sign}{format_rupiah(abs(diff))}`{pct_str}"

    lines = [
        "⚖️ *PERBANDINGAN PERIODE KEUANGAN*",
        f"• *{p1.label}*: `{format_display(p1.start_date)}` s.d. `{format_display(p1.end_date)}`",
        f"• *{p2.label}*: `{format_display(p2.start_date)}` s.d. `{format_display(p2.end_date)}`\n",
        "*Ringkasan Arus Kas:*",
        f"• Pemasukan: `{format_rupiah(p1.total_income)}` ➔ `{format_rupiah(p2.total_income)}`",
        f"  Perubahan: {_diff_str(rep.income_diff, rep.income_diff_pct)}",
        f"• Pengeluaran: `{format_rupiah(p1.total_expense)}` ➔ `{format_rupiah(p2.total_expense)}`",
        f"  Perubahan: {_diff_str(rep.expense_diff, rep.expense_diff_pct)}",
        f"• Arus Kas Net: `{format_rupiah(p1.net)}` ➔ `{format_rupiah(p2.net)}`",
        f"  Perubahan: {_diff_str(rep.net_diff, None)}",
        f"• Transaksi: {p1.transaction_count} ➔ {p2.transaction_count} transaksi\n",
    ]

    if rep.category_comparisons:
        lines.append("*Perubahan Pengeluaran per Kategori:*")
        for c in rep.category_comparisons:
            trend = "🔺" if c.diff_amount > 0 else "🔻" if c.diff_amount < 0 else "="
            sign = "+" if c.diff_amount > 0 else "-" if c.diff_amount < 0 else ""
            pct_str = (
                f" ({sign}{abs(c.percentage_change):.1f}%)"
                if c.percentage_change is not None
                else " (Baru)"
            )
            diff_display = f"`{sign}{format_rupiah(abs(c.diff_amount))}`{pct_str}"
            lines.append(
                f"{trend} *{c.category_name}*: `{format_rupiah(c.period1_amount)}` "
                f"➔ `{format_rupiah(c.period2_amount)}`\n"
                f"   Selisih: {diff_display}"
            )

    return "\n".join(lines)


def _format_bot_statistics(stats: FinancialStatistics) -> str:
    start_str = format_display(stats.start_date)
    end_str = format_display(stats.end_date)
    net_sign = "+" if stats.net > 0 else ""

    lines = [
        "📈 *STATISTIK KEUANGAN & INSIGHT*",
        f"Periode: `{start_str}` s.d. `{end_str}` ({stats.total_days} hari)\n",
        "*Aktivitas Transaksi:*",
        f"• Total Pemasukan   : `{format_rupiah(stats.total_income)}` ({stats.income_count} tx)",
        f"• Total Pengeluaran : `{format_rupiah(stats.total_expense)}` ({stats.expense_count} tx)",
        f"• Arus Kas Bersih   : `{net_sign}{format_rupiah(stats.net)}`",
        f"• Hari Belanja      : {stats.spending_days} hari "
        f"(Bebas Belanja: {stats.zero_spending_days} hari)\n",
        "*Rata-rata & Proyeksi:*",
        f"• Rata-rata Belanja / Hari : `{format_rupiah(stats.avg_daily_expense)}`",
        f"• Rata-rata per Transaksi  : `{format_rupiah(stats.avg_expense_per_tx)}`",
    ]

    if stats.projected_month_expense is not None:
        lines.append(
            f"• Proyeksi Belanja Akhir Bulan : `{format_rupiah(stats.projected_month_expense)}`"
        )

    lines.append("\n*Nilai Ekstrem:*")
    if stats.max_expense:
        lines.append(
            f"• Pengeluaran Terbesar : `{format_rupiah(stats.max_expense.amount)}` "
            f"({stats.max_expense.category_name}, {format_display(stats.max_expense.date)})"
        )
    if stats.min_expense:
        lines.append(
            f"• Pengeluaran Terkecil : `{format_rupiah(stats.min_expense.amount)}` "
            f"({stats.min_expense.category_name}, {format_display(stats.min_expense.date)})"
        )
    if stats.max_income:
        lines.append(
            f"• Pemasukan Terbesar   : `{format_rupiah(stats.max_income.amount)}` "
            f"({stats.max_income.category_name}, {format_display(stats.max_income.date)})"
        )
    if stats.max_spending_day:
        d, amt = stats.max_spending_day
        lines.append(f"• Hari Paling Boros    : `{format_display(d)}` (`{format_rupiah(amt)}`)")

    if stats.category_expenses:
        lines.append("\n*Distribusi Pengeluaran:*")
        for c in stats.category_expenses:
            lines.append(f"• {c.category_name}: `{format_rupiah(c.amount)}` ({c.percentage:.1f}%)")

    return "\n".join(lines)


@whitelist_required
async def cmd_harian(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan laporan keuangan harian."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_daily() -> FinancialReport:
        with session_scope(factory) as session:
            return get_daily_report(session, user_id)

    rep = await asyncio.to_thread(_get_daily)
    text = _format_bot_report("HARIAN", rep)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_perbandingan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan perbandingan keuangan bulan ini vs bulan lalu."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_comp() -> PeriodComparisonReport:
        with session_scope(factory) as session:
            return get_monthly_comparison(session, user_id)

    rep = await asyncio.to_thread(_get_comp)
    text = _format_bot_comparison(rep)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_statistik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan statistik keuangan bulan ini."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_stat() -> FinancialStatistics:
        with session_scope(factory) as session:
            return get_financial_statistics(session, user_id)

    stats = await asyncio.to_thread(_get_stat)
    text = _format_bot_statistics(stats)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_grafik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buat dan kirim 4 grafik keuangan periode bulan ini ke chat Telegram."""
    if not update.effective_message:
        return
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    wait_msg = await update.effective_message.reply_text("⏳ Sedang membuat 4 grafik keuangan...")

    def _make_charts() -> dict[str, Any]:
        with session_scope(factory) as session:
            from database import queries

            user = queries.get_user(session, user_id)
            tz_name = user.timezone if user else config.TIMEZONE_NAME
            t = today(tz_name)
            start_date = date(t.year, t.month, 1)
            # akhir bulan atau hari ini
            return generate_all_charts(session, user_id, start_date, t, prefix="tg")

    try:
        charts_map = await asyncio.to_thread(_make_charts)
    except Exception:
        logger.exception("Gagal membuat grafik keuangan")
        await wait_msg.edit_text("❌ Terjadi kesalahan saat memproses grafik keuangan.")
        return

    captions = {
        "expense_by_category": "🔴 Distribusi Pengeluaran per Kategori (Bulan Ini)",
        "income_by_category": "🟢 Distribusi Pemasukan per Kategori (Bulan Ini)",
        "income_vs_expense": "📊 Arus Kas Harian (Pemasukan vs Pengeluaran)",
        "balance_trend": "📈 Perkembangan Saldo Kumulatif",
    }

    try:
        keys = (
            "expense_by_category",
            "income_by_category",
            "income_vs_expense",
            "balance_trend",
        )
        for key in keys:
            path = charts_map.get(key)
            if path and path.exists():
                with open(path, "rb") as photo_file:
                    await update.effective_message.reply_photo(
                        photo=photo_file,
                        caption=captions.get(key, "Grafik Keuangan"),
                    )
        await wait_msg.delete()
    except Exception:
        logger.exception("Gagal mengirim foto grafik")
        await update.effective_message.reply_text("❌ Gagal mengirim gambar grafik ke chat.")


@whitelist_required
async def cmd_runway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan metrik runway, laju harian, proyeksi, dan perkiraan tanggal habis (/runway)."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_runway_info() -> str:
        with session_scope(factory) as session:
            from services.dashboard import get_runway_metrics

            metrics = get_runway_metrics(session, user_id)
            month_name = metrics.as_of.strftime("%B %Y")
            lines = [
                f"🚀 *RUNWAY & PREDIKSI KEUANGAN* ({month_name})\n",
                f"• Laju Pengeluaran : `{format_rupiah(metrics.daily_burn_rate)}`/hari",
                f"• Proyeksi Akhir Bulan : `{format_rupiah(metrics.projected_monthly_spend)}`",
                f"• Total Saldo Kas : `{format_rupiah(metrics.total_balance)}`",
            ]
            if metrics.budget_amount is not None:
                lines.append(
                    f"• Anggaran (Budget) : `{format_rupiah(metrics.budget_amount)}` "
                    f"(terpakai: `{format_rupiah(metrics.total_expense)}`)"
                )
                if metrics.recommended_daily_limit is not None:
                    rec_str = format_rupiah(metrics.recommended_daily_limit)
                    lines.append(f"• Batas Harian Disarankan : `{rec_str}`/hari")
                if metrics.is_budget_exceeded:
                    lines.append("• Status Anggaran : ⚠️ *Sudah terlampaui*")
                elif metrics.budget_exhaustion_date:
                    exp_date = metrics.budget_exhaustion_date.strftime("%d-%m-%Y")
                    lines.append(
                        f"• Perkiraan Anggaran Habis : `{exp_date}` "
                        f"({metrics.budget_days_left} hari lagi)"
                    )
                else:
                    lines.append("• Status Anggaran : ✅ *Aman (tidak diproyeksikan habis)*")

            if metrics.is_balance_exhausted:
                lines.append("\n⚠️ *Perhatian: Saldo kas saat ini telah habis.*")
            elif metrics.balance_runway_days is not None:
                exp_date_str = (
                    metrics.balance_exhaustion_date.strftime("%d-%m-%Y")
                    if metrics.balance_exhaustion_date
                    else "-"
                )
                lines.append(
                    f"\n⏳ *Daya Tahan Saldo (Runway):* `{metrics.balance_runway_days} hari` "
                    f"(perkiraan habis: `{exp_date_str}`)"
                )
            else:
                lines.append("\n✅ *Runway Saldo Kas:* Aman (pengeluaran Rp 0 / saldo bertahan)")

            return "\n".join(lines)

    text = await asyncio.to_thread(_get_runway_info)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_anomali(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan daftar transaksi pengeluaran yang terdeteksi tidak wajar (/anomali)."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _get_anomalies_info() -> str:
        with session_scope(factory) as session:
            from services.anomalies import list_anomalous_transactions

            items = list_anomalous_transactions(session, user_id, limit=20)
            if not items:
                return (
                    "✅ *Tidak Ada Pengeluaran Tidak Wajar*\n\n"
                    "Semua transaksi pengeluaran Anda berada dalam rentang statistik wajar."
                )

            lines = [f"⚠️ *DETEKSI PENGELUARAN TIDAK WAJAR* (Ditemukan {len(items)} transaksi)\n"]
            for it in items:
                tx = it.transaction
                tag = "🚨 EKSTREM" if it.severity == "extreme" else "⚠️ PERINGATAN"
                cat_name = tx.category.name if tx.category else "Tanpa Kategori"
                date_str = format_display(tx.date)
                lines.append(
                    f"• *#{tx.id}* `{format_rupiah(tx.amount)}` ({cat_name}, {date_str})\n"
                    f"  Status : *{tag}*\n"
                    f"  Analisis : {it.reason}\n"
                )
            return "\n".join(lines)

    text = await asyncio.to_thread(_get_anomalies_info)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
