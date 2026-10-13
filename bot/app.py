"""Inisialisasi aplikasi Telegram Bot dan registrasi seluruh handler."""

from __future__ import annotations

import datetime as dt
import logging
import traceback
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from bot.handlers import (
    backup_callback,
    cmd_alias,
    cmd_anomali,
    cmd_backup,
    cmd_backup_penuh,
    cmd_batal,
    cmd_blokir,
    cmd_buat_undangan,
    cmd_budget,
    cmd_budget_mingguan,
    cmd_ekspor_data,
    cmd_export,
    cmd_export_csv,
    cmd_export_excel,
    cmd_export_pdf,
    cmd_grafik,
    cmd_hapus_akun,
    cmd_hapus_alias,
    cmd_hari_ini,
    cmd_harian,
    cmd_help,
    cmd_kode,
    cmd_konsistensi,
    cmd_laporan,
    cmd_pengguna,
    cmd_pengingat,
    cmd_perbandingan,
    cmd_restore,
    cmd_ringkasan,
    cmd_riwayat,
    cmd_riwayat_perubahan,
    cmd_rollover_budget,
    cmd_runway,
    cmd_saldo,
    cmd_setujui,
    cmd_start,
    cmd_statistik,
    cmd_status,
    cmd_tambah_alias,
    cmd_undo,
    cmd_waktu_pengingat,
    cmd_zona_waktu,
    export_callback,
    get_budget_conversation_handler,
    get_transaction_conversation_handler,
    get_transfer_conversation_handler,
    handle_backup_document,
    handle_text_menu,
    quick_cancel_callback,
    quick_confirm_callback,
    register_system_backup_job,
    reload_scheduled_jobs,
    reminder_callback,
    report_callback,
)
from database import queries
from database.engine import session_scope
from models import USER_STATUS_ACTIVE

logger = logging.getLogger(__name__)


def create_bot_app(factory: Any, token: str | None = None) -> Application:
    """Bangun dan konfigurasikan instance Telegram Application."""
    active_token = token or config.TELEGRAM_BOT_TOKEN
    if not active_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN belum diatur di file .env atau variabel lingkungan. "
            "Dapatkan token dari @BotFather dan masukkan ke .env."
        )

    app = ApplicationBuilder().token(active_token).build()
    app.bot_data["session_factory"] = factory

    # 1. Daftarkan conversation handler terlebih dahulu (prioritas tinggi)
    app.add_handler(get_transaction_conversation_handler())
    app.add_handler(get_transfer_conversation_handler())
    app.add_handler(get_budget_conversation_handler())

    # 2. Daftarkan handler perintah cepat
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["kode", "undangan"], cmd_kode))
    app.add_handler(CommandHandler(["buat_undangan", "invite"], cmd_buat_undangan))
    app.add_handler(CommandHandler(["setujui", "approve"], cmd_setujui))
    app.add_handler(CommandHandler(["blokir", "block"], cmd_blokir))
    app.add_handler(CommandHandler(["pengguna", "users"], cmd_pengguna))
    app.add_handler(CommandHandler(["status", "server_status"], cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("hari_ini", cmd_hari_ini))
    app.add_handler(CommandHandler("riwayat", cmd_riwayat))
    app.add_handler(CommandHandler(["riwayat_perubahan", "audit"], cmd_riwayat_perubahan))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("laporan", cmd_laporan))
    app.add_handler(CommandHandler(["harian", "laporan_harian"], cmd_harian))
    app.add_handler(CommandHandler(["perbandingan", "banding"], cmd_perbandingan))
    app.add_handler(CommandHandler(["statistik", "stats"], cmd_statistik))
    app.add_handler(CommandHandler("grafik", cmd_grafik))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler(["budget_mingguan", "weekly_budget"], cmd_budget_mingguan))
    app.add_handler(CommandHandler(["rollover_budget", "rollover"], cmd_rollover_budget))
    app.add_handler(CommandHandler(["pengingat", "reminders"], cmd_pengingat))
    app.add_handler(CommandHandler(["waktu_pengingat", "jam_pengingat"], cmd_waktu_pengingat))
    app.add_handler(CommandHandler(["zona_waktu", "timezone", "tz"], cmd_zona_waktu))
    app.add_handler(CommandHandler(["ringkasan", "summary"], cmd_ringkasan))
    app.add_handler(CommandHandler(["runway", "burn_rate"], cmd_runway))
    app.add_handler(CommandHandler(["anomali", "outlier"], cmd_anomali))
    app.add_handler(CommandHandler(["export", "ekspor"], cmd_export))
    app.add_handler(CommandHandler(["export_csv", "csv"], cmd_export_csv))
    app.add_handler(CommandHandler(["export_excel", "excel", "xlsx"], cmd_export_excel))
    app.add_handler(CommandHandler(["export_pdf", "pdf"], cmd_export_pdf))
    app.add_handler(CommandHandler(["ekspor_data", "export_data"], cmd_ekspor_data))
    app.add_handler(CommandHandler(["backup", "cadangkan"], cmd_backup))
    app.add_handler(CommandHandler(["backup_penuh", "full_backup"], cmd_backup_penuh))
    app.add_handler(CommandHandler(["restore", "pulihkan"], cmd_restore))
    app.add_handler(CommandHandler("hapus_akun", cmd_hapus_akun))
    app.add_handler(CommandHandler(["konsistensi", "cek_saldo"], cmd_konsistensi))
    app.add_handler(CommandHandler(["alias", "aliases"], cmd_alias))
    app.add_handler(CommandHandler("tambah_alias", cmd_tambah_alias))
    app.add_handler(CommandHandler("hapus_alias", cmd_hapus_alias))
    app.add_handler(CommandHandler("batal", cmd_batal))
    app.add_handler(CommandHandler("cancel", cmd_batal))

    # 3. Callback queries
    app.add_handler(
        CallbackQueryHandler(report_callback, pattern="^rep_(daily|weekly|monthly|compare|stats)$")
    )
    app.add_handler(CallbackQueryHandler(reminder_callback, pattern="^rem_"))
    app.add_handler(CallbackQueryHandler(export_callback, pattern="^exp_"))
    app.add_handler(CallbackQueryHandler(backup_callback, pattern="^bkp_"))
    app.add_handler(CallbackQueryHandler(quick_confirm_callback, pattern="^quick_confirm$"))
    app.add_handler(CallbackQueryHandler(quick_cancel_callback, pattern="^quick_cancel$"))

    # 4. Message handler untuk dokumen (file backup JSON) & teks tombol Reply Keyboard
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_menu))

    # 5. Global error handler
    app.add_error_handler(global_error_handler)

    # 6. Muat ulang jadwal pengingat & ringkasan dari database ke JobQueue (V3)
    reload_scheduled_jobs(app, factory)

    # 7. Daftarkan job cadangan dan retensi sistem harian
    register_system_backup_job(app)

    return app


def get_admin_telegram_ids(factory: Any | None) -> set[int]:
    """Kumpulkan seluruh ID Telegram milik administrator (dari config & basis data)."""
    admin_ids: set[int] = set()
    if config.ALLOWED_TELEGRAM_IDS:
        for tid in config.ALLOWED_TELEGRAM_IDS:
            try:
                admin_ids.add(int(tid))
            except (ValueError, TypeError):
                continue
    if factory is not None:
        try:
            with session_scope(factory) as session:
                users = queries.list_users(session, status=USER_STATUS_ACTIVE)
                for u in users:
                    if u.role == "admin" and u.telegram_id:
                        admin_ids.add(int(u.telegram_id))
        except Exception as exc:
            logger.debug("Tidak dapat membaca data admin dari basis data: %s", exc)
    return admin_ids


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangani error tak terduga secara global dan kirim notifikasi error ke admin via Telegram."""
    err = context.error
    logger.error(
        "Terjadi error tak terduga saat memproses update: %s",
        err,
        exc_info=err,
    )

    # Notifikasi pengguna ramah
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Terjadi kesalahan pada sistem. "
                "Administrator telah menerima laporan kendala ini."
            )
        except Exception:
            logger.warning("Gagal mengirim notifikasi error ke chat Telegram pengguna")

    # Notifikasi ke administrator via Telegram
    bot = getattr(context, "bot", None)
    if not bot:
        return

    factory = None
    if getattr(context, "application", None) and hasattr(context.application, "bot_data"):
        factory = context.application.bot_data.get("session_factory")

    admin_tg_ids = get_admin_telegram_ids(factory)
    if not admin_tg_ids:
        return

    if err:
        tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    else:
        tb_str = "N/A"
    tb_snippet = tb_str[-1200:] if len(tb_str) > 1200 else tb_str

    user_info = "N/A"
    if isinstance(update, Update) and update.effective_user:
        u = update.effective_user
        user_info = f"{u.first_name or ''} (ID: `{u.id}`, @{u.username or '-'})"

    cmd_info = "N/A"
    if isinstance(update, Update) and update.effective_message and update.effective_message.text:
        cmd_info = update.effective_message.text[:80]

    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_text = (
        "🚨 *NOTIFIKASI ERROR SISTEM*\n\n"
        f"• *Waktu*: `{now_str}`\n"
        f"• *Pengguna*: {user_info}\n"
        f"• *Perintah/Pesan*: `{cmd_info}`\n"
        f"• *Tipe Error*: `{type(err).__name__}`\n"
        f"• *Pesan Error*: `{str(err)[:250]}`\n\n"
        f"*Traceback:*\n```python\n{tb_snippet}\n```"
    )

    for admin_chat_id in admin_tg_ids:
        try:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=alert_text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            try:
                plain_alert = (
                    f"🚨 NOTIFIKASI ERROR SISTEM\n"
                    f"Waktu: {now_str}\n"
                    f"Pengguna: {user_info}\n"
                    f"Error: {type(err).__name__}: {str(err)[:250]}\n\n"
                    f"Traceback:\n{tb_snippet}"
                )
                await bot.send_message(chat_id=admin_chat_id, text=plain_alert)
            except Exception as send_exc:
                logger.warning(
                    "Gagal mengirim error alert ke admin %s: %s",
                    admin_chat_id,
                    send_exc,
                )


def run_bot(factory: Any, token: str | None = None) -> None:
    """Jalankan polling Telegram bot secara sinkron/blocking."""
    app = create_bot_app(factory, token=token)
    logger.info("Memulai polling Telegram bot...")
    app.run_polling(drop_pending_updates=True)
