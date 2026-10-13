"""Handler Telegram Bot untuk Pengingat, Penjadwalan (JobQueue), dan Zona Waktu (V2 & V3)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import whitelist_required
from bot.keyboards import reminder_settings_keyboard, timezone_selection_keyboard
from database import queries
from database.engine import session_scope
from models import USER_STATUS_ACTIVE, ReminderSetting, UserSetting
from services import reminders
from utils.dates import (
    format_display,
    format_timezone_label,
    normalize_timezone,
    today,
)
from utils.errors import ValidationError

logger = logging.getLogger(__name__)

USER_JOB_SUFFIXES = ("daily", "recurring", "weekly", "monthly")


def _extract_args(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Ambil argumen dari `context.args` atau teks pesan."""
    args = getattr(context, "args", None)
    if args:
        return [str(a) for a in args]
    if update.effective_message and update.effective_message.text:
        parts = update.effective_message.text.strip().split()
        if len(parts) > 1 and parts[0].startswith("/"):
            return parts[1:]
    return []


def _format_settings_message(setting: UserSetting | ReminderSetting) -> str:
    """Format teks tampilan pengaturan pengingat, penjadwal, dan zona waktu."""
    d_stat = "✅ Aktif" if setting.is_daily_active else "❌ Nonaktif"
    w_stat = "✅ Aktif" if setting.is_weekly_active else "❌ Nonaktif"
    m_stat = "✅ Aktif" if setting.is_monthly_active else "❌ Nonaktif"
    rec_active = getattr(setting, "is_recurring_active", True)
    r_stat = "✅ Aktif" if rec_active else "❌ Nonaktif"
    tz_val = getattr(setting, "timezone", config.TIMEZONE_NAME)
    tz_label = format_timezone_label(tz_val)

    return (
        "⚙️ *PENGATURAN PENGINGAT & RINGKASAN*\n\n"
        f"• Zona Waktu           : *{tz_label}*\n"
        f"• Pengingat Harian     : *{d_stat}*\n"
        f"• Jam Pengingat Harian : *{setting.daily_time}*\n"
        f"• Pengingat Trx Rutin  : *{r_stat}*\n"
        f"• Ringkasan Mingguan   : *{w_stat}*\n"
        f"• Ringkasan Bulanan    : *{m_stat}*\n\n"
        "💡 *Tips:*\n"
        "• Gunakan tombol di bawah untuk mengatur jadwal atau mengganti zona waktu.\n"
        "• Ketik `/zona_waktu <WIB|WITA|WIT>` untuk mengatur zona waktu.\n"
        "• Ketik `/waktu_pengingat <HH:MM>` untuk mengganti jam pengingat harian "
        "(contoh: `/waktu_pengingat 21:00`).\n"
        "• Ketik `/ringkasan` untuk melihat ringkasan keuangan saat ini."
    )


def _get_job_queue(context_or_app: Any) -> Any:
    """Ambil instance `JobQueue` dari `context` atau `Application` jika tersedia."""
    jq = getattr(context_or_app, "job_queue", None)
    if jq is not None:
        return jq
    app = getattr(context_or_app, "application", None)
    if app is not None:
        return getattr(app, "job_queue", None)
    return None


def sync_user_jobs_in_queue(
    job_queue: Any,
    user_id: int,
    telegram_id: int | None,
    us: UserSetting,
) -> int:
    """Sinkronkan job terjadwal milik satu pengguna di dalam `JobQueue`."""
    if job_queue is None:
        return 0

    # Hapus job lama milik user_id ini
    for suffix in USER_JOB_SUFFIXES:
        job_name = f"user_{user_id}_{suffix}"
        for old_job in job_queue.get_jobs_by_name(job_name):
            old_job.schedule_removal()

    if not telegram_id:
        return 0

    try:
        tz = ZoneInfo(normalize_timezone(us.timezone))
        hour_str, min_str = us.daily_time.split(":")
        target_time = dt.time(int(hour_str), int(min_str), tzinfo=tz)
    except Exception:
        tz = ZoneInfo(config.TIMEZONE_NAME)
        target_time = dt.time(20, 0, tzinfo=tz)

    scheduled_count = 0

    if us.is_daily_active:
        job_queue.run_daily(
            scheduled_reminder_job,
            time=target_time,
            name=f"user_{user_id}_daily",
            data={"user_id": user_id, "job_type": "daily"},
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        scheduled_count += 1

    if us.is_recurring_active:
        job_queue.run_daily(
            scheduled_reminder_job,
            time=target_time,
            name=f"user_{user_id}_recurring",
            data={"user_id": user_id, "job_type": "recurring"},
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        scheduled_count += 1

    if us.is_weekly_active:
        job_queue.run_daily(
            scheduled_reminder_job,
            time=target_time,
            days=(0,),
            name=f"user_{user_id}_weekly",
            data={"user_id": user_id, "job_type": "weekly"},
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        scheduled_count += 1

    if us.is_monthly_active:
        job_queue.run_monthly(
            scheduled_reminder_job,
            when=target_time,
            day=1,
            name=f"user_{user_id}_monthly",
            data={"user_id": user_id, "job_type": "monthly"},
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        scheduled_count += 1

    return scheduled_count


def reload_scheduled_jobs(app: Any, factory: Any | None = None) -> int:
    """Muat ulang seluruh job pengingat dan ringkasan pengguna dari database ke `JobQueue`.

    Dipanggil otomatis saat bot pertama kali dibuat / restart agar semua jadwal
    pengguna aktif langsung terdaftar kembali di `JobQueue`.
    """
    job_queue = _get_job_queue(app)
    if job_queue is None:
        return 0

    active_factory = factory
    if active_factory is None and hasattr(app, "bot_data"):
        active_factory = app.bot_data.get("session_factory")
    if active_factory is None:
        return 0

    # Bersihkan seluruh job pengguna lama di job_queue sebelum memuat ulang
    for job in list(job_queue.jobs()):
        if job.name and (
            job.name.startswith("user_")
            or job.name in ("global_reminder_sweep", "system_daily_backup_retention")
        ):
            job.schedule_removal()

    total_jobs = 0
    try:
        with session_scope(active_factory) as session:
            active_users = queries.list_users(session, status=USER_STATUS_ACTIVE)
            for user in active_users:
                if not user.telegram_id:
                    continue
                us = reminders.get_user_settings(session, user.id)
                total_jobs += sync_user_jobs_in_queue(job_queue, user.id, user.telegram_id, us)
    except Exception as exc:
        logger.warning("Gagal memuat ulang job terjadwal dari database: %s", exc)
        return 0

    job_queue.run_repeating(
        scheduled_reminder_job,
        interval=3600,
        first=60,
        name="global_reminder_sweep",
    )

    return total_jobs


def register_system_backup_job(app_or_queue: Any) -> None:
    """Daftarkan job cadangan database dan retensi harian ke JobQueue aplikasi."""
    jq = _get_job_queue(app_or_queue)
    if jq is None:
        return
    for old_job in jq.get_jobs_by_name("system_daily_backup_retention"):
        old_job.schedule_removal()
    try:
        backup_time = dt.time(3, 0, tzinfo=ZoneInfo(config.TIMEZONE_NAME))
        jq.run_daily(
            scheduled_backup_retention_job,
            time=backup_time,
            name="system_daily_backup_retention",
        )
    except Exception as exc:
        logger.warning("Gagal mendaftarkan job backup retensi sistem: %s", exc)


async def scheduled_backup_retention_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job terjadwal berkala untuk mencadangkan database dan memangkas file sesuai retensi."""
    try:
        from scripts.backup_retention import run_backup_and_retention

        backup_file, pruned = await asyncio.to_thread(run_backup_and_retention)
        if backup_file:
            logger.info(
                "Backup terjadwal JobQueue berhasil: %s (dipangkas: %d file)",
                backup_file.name,
                len(pruned),
            )
    except Exception as exc:
        logger.error(
            "Gagal menjalankan scheduled_backup_retention_job: %s",
            exc,
            exc_info=exc,
        )


@whitelist_required
async def cmd_zona_waktu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan atau ubah zona waktu pengguna (`/zona_waktu [WIB|WITA|WIT]`)."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]
    args = _extract_args(update, context)

    if not args:

        def _get_current() -> tuple[str, str]:
            with session_scope(factory) as session:
                us = reminders.get_user_settings(session, user_id)
                return us.timezone, format_display(today(us.timezone))

        cur_tz, today_str = await asyncio.to_thread(_get_current)
        label = format_timezone_label(cur_tz)
        msg = (
            "🕒 *PENGATURAN ZONA WAKTU*\n\n"
            f"• Zona Waktu Saat Ini : *{label}*\n"
            f"• Tanggal Hari Ini    : `{today_str}`\n\n"
            "Pilih zona waktu Anda melalui tombol di bawah atau ketik:\n"
            "`/zona_waktu WIB` | `/zona_waktu WITA` | `/zona_waktu WIT`"
        )
        await update.effective_message.reply_text(
            msg,
            reply_markup=timezone_selection_keyboard(cur_tz),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    tz_arg = args[0].strip()

    def _update_tz() -> tuple[str, str, int | None, UserSetting]:
        with session_scope(factory) as session:
            us = reminders.set_user_timezone(session, user_id, tz_arg)
            user = queries.get_user(session, user_id)
            tid = user.telegram_id if user else None
            # Buat salinan atribut penting sebelum sesi ditutup
            session.expunge(us)
            return us.timezone, format_display(today(us.timezone)), tid, us

    try:
        new_tz, today_str, tid, us_obj = await asyncio.to_thread(_update_tz)
    except ValidationError as exc:
        await update.effective_message.reply_text(
            f"❌ Zona waktu tidak valid: {exc}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sync_user_jobs_in_queue(_get_job_queue(context), user_id, tid, us_obj)
    label = format_timezone_label(new_tz)
    await update.effective_message.reply_text(
        f"✅ Zona waktu berhasil diatur ke *{label}*.\n"
        f"📅 Tanggal hari ini di zona Anda: `{today_str}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_required
async def cmd_pengingat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan menu dan status pengaturan pengingat & ringkasan otomatis."""
    if not update.effective_message:
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _sync() -> tuple[str, Any]:
        with session_scope(factory) as session:
            us = reminders.get_user_settings(session, user_id)
            return _format_settings_message(us), reminder_settings_keyboard(us)

    text, reply_markup = await asyncio.to_thread(_sync)
    await update.effective_message.reply_text(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def cmd_waktu_pengingat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ubah waktu pengingat harian (format HH:MM)."""
    if not update.effective_message:
        return

    args = _extract_args(update, context)
    if not args:
        await update.effective_message.reply_text(
            "⚠️ Masukkan jam pengingat dalam format `HH:MM`.\nContoh: `/waktu_pengingat 21:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    time_str = args[0].strip()
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _sync() -> tuple[str, int | None, UserSetting | None]:
        with session_scope(factory) as session:
            try:
                us = reminders.update_user_settings(session, user_id, daily_time=time_str)
                user = queries.get_user(session, user_id)
                tid = user.telegram_id if user else None
                msg = f"✅ Jam pengingat harian berhasil diubah ke *{us.daily_time}*."
                session.expunge(us)
                return msg, tid, us
            except ValidationError as e:
                return f"❌ Format waktu tidak valid: {e}", None, None

    message, tid, us_obj = await asyncio.to_thread(_sync)
    if us_obj is not None:
        sync_user_jobs_in_queue(_get_job_queue(context), user_id, tid, us_obj)
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def cmd_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kirimkan ringkasan keuangan otomatis (mingguan atau bulanan)."""
    if not update.effective_message:
        return

    args = _extract_args(update, context)
    arg = args[0].lower() if args else ""
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    def _sync() -> list[str]:
        with session_scope(factory) as session:
            messages: list[str] = []
            if arg in ("minggu", "mingguan", "week", "weekly"):
                w = reminders.build_weekly_summary(session, user_id)
                messages.append(w.message)
            elif arg in ("bulan", "bulanan", "month", "monthly"):
                m = reminders.build_monthly_summary(session, user_id)
                messages.append(m.message)
            else:
                # Default: kirim ringkasan mingguan dan bulanan sekaligus
                w = reminders.build_weekly_summary(session, user_id)
                m = reminders.build_monthly_summary(session, user_id)
                messages.append(w.message)
                messages.append(m.message)
            return messages

    results = await asyncio.to_thread(_sync)
    for msg in results:
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


@whitelist_required
async def reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tanggapi aksi callback tombol inline pengaturan pengingat & zona waktu."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    action = query.data
    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data["db_user_id"]

    if action in (
        "rem_toggle_daily",
        "rem_toggle_weekly",
        "rem_toggle_monthly",
        "rem_toggle_recurring",
    ) or action.startswith("rem_tz_"):

        def _toggle_sync() -> tuple[str, Any, int | None, UserSetting]:
            with session_scope(factory) as session:
                us = reminders.get_user_settings(session, user_id)
                if action == "rem_toggle_daily":
                    us = reminders.update_user_settings(
                        session, user_id, is_daily_active=not us.is_daily_active
                    )
                elif action == "rem_toggle_weekly":
                    us = reminders.update_user_settings(
                        session, user_id, is_weekly_active=not us.is_weekly_active
                    )
                elif action == "rem_toggle_monthly":
                    us = reminders.update_user_settings(
                        session, user_id, is_monthly_active=not us.is_monthly_active
                    )
                elif action == "rem_toggle_recurring":
                    us = reminders.update_user_settings(
                        session, user_id, is_recurring_active=not us.is_recurring_active
                    )
                elif action.startswith("rem_tz_"):
                    tz_code = action.removeprefix("rem_tz_")
                    us = reminders.set_user_timezone(session, user_id, tz_code)

                user = queries.get_user(session, user_id)
                tid = user.telegram_id if user else None
                text_out = _format_settings_message(us)
                kb_out = reminder_settings_keyboard(us)
                session.expunge(us)
                return text_out, kb_out, tid, us

        new_text, new_markup, tid, us_obj = await asyncio.to_thread(_toggle_sync)
        sync_user_jobs_in_queue(_get_job_queue(context), user_id, tid, us_obj)
        await query.edit_message_text(
            new_text, reply_markup=new_markup, parse_mode=ParseMode.MARKDOWN
        )

    elif action == "rem_preview_daily":

        def _daily_sync() -> str:
            with session_scope(factory) as session:
                payload = reminders.build_daily_reminder(session, user_id)
                return payload.message

        msg = await asyncio.to_thread(_daily_sync)
        if query.message:
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    elif action == "rem_preview_weekly":

        def _weekly_sync() -> str:
            with session_scope(factory) as session:
                payload = reminders.build_weekly_summary(session, user_id)
                return payload.message

        msg = await asyncio.to_thread(_weekly_sync)
        if query.message:
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    elif action == "rem_preview_monthly":

        def _monthly_sync() -> str:
            with session_scope(factory) as session:
                payload = reminders.build_monthly_summary(session, user_id)
                return payload.message

        msg = await asyncio.to_thread(_monthly_sync)
        if query.message:
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    elif action == "rem_preview_recurring":

        def _rec_sync() -> str:
            with session_scope(factory) as session:
                payload = reminders.build_recurring_due_notification(session, user_id)
                return payload.message

        msg = await asyncio.to_thread(_rec_sync)
        if query.message:
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def scheduled_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pekerjaan berkala (`JobQueue`) untuk mengirimkan pengingat dan ringkasan secara idempoten."""
    factory = context.application.bot_data.get("session_factory")
    if not factory:
        return

    job = getattr(context, "job", None)
    job_data = getattr(job, "data", None) if job is not None else None
    target_user_id: int | None = None
    target_job_type: str | None = None
    if isinstance(job_data, dict):
        target_user_id = job_data.get("user_id")
        target_job_type = job_data.get("job_type")

    def _sync_check() -> list[tuple[int, str, str]]:
        """Kumpulkan pesan-pesan yang siap dikirim: `(telegram_id, msg, notification_type)`."""
        to_send: list[tuple[int, str, str]] = []
        with session_scope(factory) as session:
            if target_user_id is not None:
                user = queries.get_user(session, target_user_id)
                if not user or user.status != USER_STATUS_ACTIVE or not user.telegram_id:
                    return []
                items = reminders.collect_due_notifications(
                    session, user.id, job_type=target_job_type
                )
                for item in items:
                    if item.telegram_id:
                        to_send.append((item.telegram_id, item.message, item.notification_type))
                return to_send

            # Sweep seluruh pengguna aktif yang memiliki notifikasi aktif
            active_users = queries.list_users(session, status=USER_STATUS_ACTIVE)
            for user in active_users:
                if not user.telegram_id:
                    continue
                items = reminders.collect_due_notifications(session, user.id)
                for item in items:
                    if item.telegram_id:
                        to_send.append((item.telegram_id, item.message, item.notification_type))

        return to_send

    messages = await asyncio.to_thread(_sync_check)
    for tid, msg, _ in messages:
        try:
            await context.bot.send_message(chat_id=tid, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning("Gagal mengirim notifikasi terjadwal ke telegram_id=%d: %s", tid, e)
