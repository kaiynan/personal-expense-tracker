"""Service pengingat harian, transaksi rutin jatuh tempo, ringkasan otomatis, dan zona waktu.

Menyediakan logika bisnis untuk:
1. Pengaturan zona waktu (WIB/WITA/WIT) dan preferensi pengingat (`user_settings`).
2. Pembuatan payload & pesan pengingat harian (daily reminder).
3. Pembuatan payload & pesan transaksi rutin jatuh tempo (recurring due notification).
4. Pembuatan payload & pesan ringkasan mingguan otomatis (weekly summary).
5. Pembuatan payload & pesan ringkasan bulanan otomatis beserta insight.
6. Pencatatan pengiriman notifikasi yang idempoten (`sent_notifications`) agar tahan restart.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from database.queries import (
    get_reminder_setting,
    get_sent_notification,
    get_user_setting,
    list_transactions,
)
from models import (
    NOTIFICATION_DAILY,
    NOTIFICATION_MONTHLY,
    NOTIFICATION_PREDICTIVE_BUDGET,
    NOTIFICATION_PREDICTIVE_RUNWAY,
    NOTIFICATION_RECURRING,
    NOTIFICATION_TYPES,
    NOTIFICATION_WEEKLY,
    REMINDER_DAILY,
    REMINDER_MONTHLY,
    REMINDER_WEEKLY,
    TYPE_EXPENSE,
    TYPE_INCOME,
    ReminderSetting,
    SentNotification,
    User,
    UserSetting,
)
from reports.financial import (
    get_monthly_comparison,
    get_monthly_report,
    get_weekly_report,
)
from services import budgets, recurring
from services.common import require_user, resolve_date
from utils.dates import format_display, normalize_timezone
from utils.errors import ValidationError
from utils.formatting import format_rupiah


@dataclass(frozen=True)
class DailyReminderPayload:
    """Hasil pembentukan pengingat harian."""

    user_id: int
    date: date
    has_transactions: bool
    transactions_count: int
    total_expense: int
    total_income: int
    due_recurring: list[tuple[str, int, str]]  # [(nama, nominal, frekuensi)]
    budget_warnings: list[str]
    message: str


@dataclass(frozen=True)
class RecurringDuePayload:
    """Hasil pengecekan & pengingat transaksi rutin yang jatuh tempo."""

    user_id: int
    date: date
    has_due: bool
    due_items: list[tuple[int, str, int, str, date]]  # [(id, nama, nominal, frekuensi, due_date)]
    applied_count: int
    message: str


@dataclass(frozen=True)
class WeeklySummaryPayload:
    """Hasil pembentukan ringkasan mingguan otomatis."""

    user_id: int
    start_date: date
    end_date: date
    total_expense: int
    total_income: int
    net_cash_flow: int
    transaction_count: int
    top_category_name: str | None
    top_category_amount: int
    message: str


@dataclass(frozen=True)
class MonthlySummaryPayload:
    """Hasil pembentukan ringkasan bulanan otomatis beserta insight."""

    user_id: int
    month: str  # "YYYY-MM"
    total_expense: int
    total_income: int
    net_cash_flow: int
    transaction_count: int
    average_daily_expense: int
    projected_monthly_expense: int
    categories_with_increase: list[tuple[str, int, int]]  # [(nama, bulan_ini, kenaikan)]
    budget_warnings: list[str]
    message: str


@dataclass(frozen=True)
class ScheduledNotificationItem:
    """Satu item notifikasi terjadwal yang siap dikirimkan ke pengguna."""

    user_id: int
    telegram_id: int | None
    notification_type: str
    period_key: str
    message: str


def _validate_time_format(time_str: str) -> str:
    cleaned = time_str.strip()
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", cleaned):
        raise ValidationError(
            f"Format jam tidak valid: '{time_str}'. Gunakan format 24 jam 'HH:MM' (mis. '20:00')."
        )
    return cleaned


def _default_period_key(notification_type: str, target_date: date) -> str:
    """Hitung kunci periode idempotensi berdasarkan tipe notifikasi dan tanggal lokal pengguna."""
    if notification_type == NOTIFICATION_WEEKLY:
        week_start = target_date - timedelta(days=target_date.weekday())
        return week_start.isoformat()
    if notification_type == NOTIFICATION_MONTHLY:
        return target_date.strftime("%Y-%m")
    return target_date.isoformat()


def _ensure_synced_settings(session: Session, user: User) -> tuple[ReminderSetting, UserSetting]:
    """Pastikan baris `reminder_settings` dan `user_settings` ada dan sinkron untuk `user`."""
    rs = get_reminder_setting(session, user.id)
    us = get_user_setting(session, user.id)

    if rs is None and us is None:
        rs = ReminderSetting(user_id=user.id)
        us = UserSetting(user_id=user.id, timezone=user.timezone)
        session.add_all([rs, us])
        session.flush()
    elif rs is None and us is not None:
        rs = ReminderSetting(
            user_id=user.id,
            is_daily_active=us.is_daily_active,
            daily_time=us.daily_time,
            is_weekly_active=us.is_weekly_active,
            is_monthly_active=us.is_monthly_active,
        )
        session.add(rs)
        session.flush()
    elif us is None and rs is not None:
        us = UserSetting(
            user_id=user.id,
            timezone=user.timezone,
            is_daily_active=rs.is_daily_active,
            daily_time=rs.daily_time,
            is_recurring_active=True,
            is_weekly_active=rs.is_weekly_active,
            is_monthly_active=rs.is_monthly_active,
        )
        session.add(us)
        session.flush()

    assert rs is not None and us is not None
    if user.timezone and us.timezone != user.timezone:
        us.timezone = user.timezone
        session.flush()
    return rs, us


def get_user_settings(session: Session, user_id: int) -> UserSetting:
    """Ambil pengaturan zona waktu & jadwal notifikasi pengguna (`user_settings`)."""
    user = require_user(session, user_id)
    _rs, us = _ensure_synced_settings(session, user)
    return us


def update_user_settings(
    session: Session,
    user_id: int,
    *,
    timezone: str | None = None,
    is_daily_active: bool | None = None,
    daily_time: str | None = None,
    is_recurring_active: bool | None = None,
    is_weekly_active: bool | None = None,
    is_monthly_active: bool | None = None,
) -> UserSetting:
    """Perbarui pengaturan zona waktu dan preferensi penjadwalan pengguna."""
    user = require_user(session, user_id)
    rs, us = _ensure_synced_settings(session, user)

    if timezone is not None:
        norm_tz = normalize_timezone(timezone)
        user.timezone = norm_tz
        us.timezone = norm_tz
    if is_daily_active is not None:
        rs.is_daily_active = is_daily_active
        us.is_daily_active = is_daily_active
    if daily_time is not None:
        clean_time = _validate_time_format(daily_time)
        rs.daily_time = clean_time
        us.daily_time = clean_time
    if is_recurring_active is not None:
        us.is_recurring_active = is_recurring_active
    if is_weekly_active is not None:
        rs.is_weekly_active = is_weekly_active
        us.is_weekly_active = is_weekly_active
    if is_monthly_active is not None:
        rs.is_monthly_active = is_monthly_active
        us.is_monthly_active = is_monthly_active

    session.flush()
    return us


def set_user_timezone(session: Session, user_id: int, timezone: str) -> UserSetting:
    """Ubah zona waktu pengguna (menerima WIB, WITA, WIT, atau nama IANA seperti Asia/Jakarta)."""
    return update_user_settings(session, user_id, timezone=timezone)


def get_reminder_settings(session: Session, user_id: int) -> ReminderSetting:
    """Ambil pengaturan pengingat pengguna, atau buat default jika belum ada."""
    user = require_user(session, user_id)
    rs, _us = _ensure_synced_settings(session, user)
    return rs


def update_reminder_settings(
    session: Session,
    user_id: int,
    is_daily_active: bool | None = None,
    daily_time: str | None = None,
    is_weekly_active: bool | None = None,
    is_monthly_active: bool | None = None,
    is_recurring_active: bool | None = None,
    timezone: str | None = None,
) -> ReminderSetting:
    """Perbarui preferensi pengingat dan ringkasan otomatis pengguna."""
    update_user_settings(
        session,
        user_id,
        timezone=timezone,
        is_daily_active=is_daily_active,
        daily_time=daily_time,
        is_recurring_active=is_recurring_active,
        is_weekly_active=is_weekly_active,
        is_monthly_active=is_monthly_active,
    )
    return get_reminder_settings(session, user_id)


def has_sent_notification(
    session: Session,
    user_id: int,
    notification_type: str,
    period_key: str,
) -> bool:
    """Cek apakah notifikasi untuk tipe dan periode tertentu sudah pernah dikirim ke `user_id`."""
    require_user(session, user_id)
    if notification_type not in NOTIFICATION_TYPES:
        raise ValidationError(
            f"Jenis notifikasi tidak valid: '{notification_type}'. "
            f"Pilihan: {', '.join(NOTIFICATION_TYPES)}."
        )
    clean_key = period_key.strip()
    if not clean_key:
        raise ValidationError("Kunci periode notifikasi tidak boleh kosong.")
    return get_sent_notification(session, user_id, notification_type, clean_key) is not None


def record_sent_notification(
    session: Session,
    user_id: int,
    notification_type: str,
    period_key: str,
) -> SentNotification:
    """Catat pengiriman notifikasi secara idempoten pada tabel `sent_notifications`."""
    require_user(session, user_id)
    if notification_type not in NOTIFICATION_TYPES:
        raise ValidationError(
            f"Jenis notifikasi tidak valid: '{notification_type}'. "
            f"Pilihan: {', '.join(NOTIFICATION_TYPES)}."
        )
    clean_key = period_key.strip()
    if not clean_key:
        raise ValidationError("Kunci periode notifikasi tidak boleh kosong.")

    existing = get_sent_notification(session, user_id, notification_type, clean_key)
    if existing is not None:
        return existing

    sn = SentNotification(
        user_id=user_id,
        notification_type=notification_type,
        period_key=clean_key,
    )
    session.add(sn)
    session.flush()
    return sn


def mark_reminder_sent(
    session: Session,
    user_id: int,
    reminder_type: str,
    sent_date: date | None = None,
    period_key: str | None = None,
) -> ReminderSetting:
    """Perbarui catatan pengiriman notifikasi terakhir dan rekam di `sent_notifications`."""
    user = require_user(session, user_id)
    if reminder_type not in NOTIFICATION_TYPES:
        raise ValidationError(
            f"Jenis pengingat tidak valid: '{reminder_type}'. "
            f"Pilihan: {', '.join(NOTIFICATION_TYPES)}."
        )

    rs, _us = _ensure_synced_settings(session, user)
    target_date = resolve_date(user, sent_date)

    if reminder_type == REMINDER_DAILY:
        rs.last_daily_sent = target_date
    elif reminder_type == REMINDER_WEEKLY:
        rs.last_weekly_sent = target_date
    elif reminder_type == REMINDER_MONTHLY:
        rs.last_monthly_sent = target_date

    resolved_key = (
        period_key.strip()
        if period_key is not None and period_key.strip()
        else _default_period_key(reminder_type, target_date)
    )
    record_sent_notification(session, user_id, reminder_type, resolved_key)
    session.flush()
    return rs


def build_daily_reminder(
    session: Session,
    user_id: int,
    as_of: date | None = None,
) -> DailyReminderPayload:
    """Susun payload dan pesan pengingat harian untuk pengguna."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)

    # 1. Transaksi yang telah dicatat hari ini
    today_txs = list_transactions(session, user_id, start=target_date, end=target_date)
    expense_txs = [t for t in today_txs if t.type == TYPE_EXPENSE]
    income_txs = [t for t in today_txs if t.type == TYPE_INCOME]
    total_exp = sum(t.amount for t in expense_txs)
    total_inc = sum(t.amount for t in income_txs)
    tx_count = len(today_txs)
    has_tx = tx_count > 0

    # 2. Cek transaksi rutin yang jatuh tempo
    all_recurring = recurring.list_recurring(session, user_id, is_active=True)
    due_recurring: list[tuple[str, int, str]] = []
    for r in all_recurring:
        due = recurring.get_next_due_date(session, user_id, r.id)
        if due is not None and due <= target_date:
            due_recurring.append((r.name, r.amount, r.frequency))

    # 3. Peringatan budget bulanan
    budget_warnings = budgets.check_budget_warnings(
        session, user_id, month=target_date.strftime("%Y-%m")
    )

    # 4. Susun pesan format Markdown
    lines = [f"🔔 *PENGINGAT KEUANGAN HARIAN* ({format_display(target_date)})\n"]

    if not has_tx:
        lines.append(
            "⚠️ *Belum Ada Transaksi Tercatat Hari Ini*\n"
            "Jangan lupa mencatat pemasukan atau pengeluaranmu hari ini "
            "agar catatan keuangan tetap rapi dan akurat!"
        )
    else:
        lines.append(
            f"✨ *Aktivitas Hari Ini ({tx_count} transaksi):*\n"
            f"• Pengeluaran : `{format_rupiah(total_exp)}`\n"
            f"• Pemasukan   : `{format_rupiah(total_inc)}`"
        )

    if due_recurring:
        lines.append("\n⏰ *Transaksi Rutin Jatuh Tempo Hari Ini:*")
        for r_name, r_amount, r_freq in due_recurring:
            lines.append(f"• *{r_name}*: `{format_rupiah(r_amount)}` ({r_freq})")

    if budget_warnings:
        lines.append("\n⚠️ *Perhatian Batas Anggaran (Budget):*")
        for bw in budget_warnings:
            lines.append(f"• {bw}")

    lines.append("\n💡 *Tips Cepat:* Ketik transaksi instan (mis. `makan 20k cash`).")
    message = "\n".join(lines)

    return DailyReminderPayload(
        user_id=user_id,
        date=target_date,
        has_transactions=has_tx,
        transactions_count=tx_count,
        total_expense=total_exp,
        total_income=total_inc,
        due_recurring=due_recurring,
        budget_warnings=budget_warnings,
        message=message,
    )


def build_weekly_summary(
    session: Session,
    user_id: int,
    as_of: date | None = None,
) -> WeeklySummaryPayload:
    """Susun payload dan pesan ringkasan mingguan otomatis."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)
    rep = get_weekly_report(session, user_id, on=target_date)

    top_cat_name = None
    top_cat_amt = 0
    if rep.category_expenses:
        top_cat = rep.category_expenses[0]
        top_cat_name = top_cat.category_name
        top_cat_amt = top_cat.amount

    sign = "+" if rep.net > 0 else ""
    lines = [
        "📊 *RINGKASAN KEUANGAN MINGGUAN*\n"
        f"Periode: {format_display(rep.start_date)} s.d. {format_display(rep.end_date)}\n",
        f"• Total Pemasukan   : `{format_rupiah(rep.total_income)}`",
        f"• Total Pengeluaran : `{format_rupiah(rep.total_expense)}`",
        f"• Selisih (Net)     : `{sign}{format_rupiah(rep.net)}`",
        f"• Total Transaksi   : {rep.transaction_count} transaksi",
    ]

    if top_cat_name:
        lines.append(
            f"\n📂 *Pengeluaran Terbesar:* {top_cat_name} (`{format_rupiah(top_cat_amt)}`)"
        )

    message = "\n".join(lines)

    return WeeklySummaryPayload(
        user_id=user_id,
        start_date=rep.start_date,
        end_date=rep.end_date,
        total_expense=rep.total_expense,
        total_income=rep.total_income,
        net_cash_flow=rep.net,
        transaction_count=rep.transaction_count,
        top_category_name=top_cat_name,
        top_category_amount=top_cat_amt,
        message=message,
    )


def build_monthly_summary(
    session: Session,
    user_id: int,
    as_of: date | None = None,
) -> MonthlySummaryPayload:
    """Susun payload dan pesan ringkasan bulanan otomatis beserta insight V2."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)
    month_str = target_date.strftime("%Y-%m")
    rep = get_monthly_report(session, user_id, month=month_str)

    days_passed = target_date.day
    total_days = calendar.monthrange(target_date.year, target_date.month)[1]

    # Insight 1: Rata-rata pengeluaran harian
    avg_daily = (rep.total_expense // days_passed) if days_passed > 0 else 0

    # Insight 2: Proyeksi total pengeluaran akhir bulan
    proj_monthly = (
        ((rep.total_expense * total_days) // days_passed) if days_passed > 0 else rep.total_expense
    )

    # Insight 3: Kategori yang naik dibanding bulan lalu
    comp = get_monthly_comparison(session, user_id, month2=month_str)
    increased_cats: list[tuple[str, int, int]] = []
    for c in comp.category_comparisons:
        if c.diff_amount > 0:
            increased_cats.append((c.category_name, c.period2_amount, c.diff_amount))
    increased_cats.sort(key=lambda x: x[2], reverse=True)

    # Budget warnings
    budget_warnings = budgets.check_budget_warnings(session, user_id, month=month_str)

    sign = "+" if rep.net > 0 else ""
    month_name = calendar.month_name[target_date.month]
    lines = [
        f"🗓️ *RINGKASAN & INSIGHT KEUANGAN BULANAN* ({month_name} {target_date.year})\n",
        f"• Total Pemasukan   : `{format_rupiah(rep.total_income)}`",
        f"• Total Pengeluaran : `{format_rupiah(rep.total_expense)}`",
        f"• Selisih (Net)     : `{sign}{format_rupiah(rep.net)}`",
        f"• Jumlah Transaksi  : {rep.transaction_count} transaksi",
        "\n💡 *Insight Keuangan V2:*",
        f"• Rata-rata Pengeluaran/Hari: `{format_rupiah(avg_daily)}`",
        f"• Proyeksi Pengeluaran Akhir Bulan: `{format_rupiah(proj_monthly)}`",
    ]

    if increased_cats:
        lines.append("\n📈 *Kategori Naik Dibanding Bulan Lalu:*")
        for cat_name, cur_amt, inc_amt in increased_cats[:5]:
            lines.append(f"• *{cat_name}*: `{format_rupiah(cur_amt)}` (+{format_rupiah(inc_amt)})")

    if budget_warnings:
        lines.append("\n⚠️ *Status Batas Anggaran:*")
        for bw in budget_warnings:
            lines.append(f"• {bw}")

    message = "\n".join(lines)

    return MonthlySummaryPayload(
        user_id=user_id,
        month=month_str,
        total_expense=rep.total_expense,
        total_income=rep.total_income,
        net_cash_flow=rep.net,
        transaction_count=rep.transaction_count,
        average_daily_expense=avg_daily,
        projected_monthly_expense=proj_monthly,
        categories_with_increase=increased_cats,
        budget_warnings=budget_warnings,
        message=message,
    )


def build_recurring_due_notification(
    session: Session,
    user_id: int,
    as_of: date | None = None,
    auto_apply: bool = False,
) -> RecurringDuePayload:
    """Susun payload dan pesan pengingat transaksi rutin yang jatuh tempo."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)

    all_recurring = recurring.list_recurring(session, user_id, is_active=True)
    due_items: list[tuple[int, str, int, str, date]] = []
    for r in all_recurring:
        due = recurring.get_next_due_date(session, user_id, r.id)
        if due is not None and due <= target_date:
            due_items.append((r.id, r.name, r.amount, r.frequency, due))

    applied_count = 0
    if auto_apply and due_items:
        applied_results = recurring.process_due_recurring(session, user_id, as_of=target_date)
        applied_count = len(applied_results)

    has_due = len(due_items) > 0
    lines = [f"⏰ *TRANSAKSI RUTIN JATUH TEMPO* ({format_display(target_date)})\n"]
    if not has_due:
        lines.append("Tidak ada transaksi rutin yang jatuh tempo pada tanggal ini.")
    else:
        for r_id, r_name, r_amt, r_freq, r_due in due_items:
            lines.append(
                f"• `#{r_id}` *{r_name}*: `{format_rupiah(r_amt)}` "
                f"({r_freq}, jatuh tempo {format_display(r_due)})"
            )
        if applied_count > 0:
            lines.append(
                f"\n✅ *{applied_count} transaksi rutin* telah diproses dan dicatat otomatis."
            )

    return RecurringDuePayload(
        user_id=user_id,
        date=target_date,
        has_due=has_due,
        due_items=due_items,
        applied_count=applied_count,
        message="\n".join(lines),
    )


def collect_due_notifications(
    session: Session,
    user_id: int,
    as_of: date | None = None,
    job_type: str | None = None,
) -> list[ScheduledNotificationItem]:
    """Kumpulkan dan tandai notifikasi terjadwal yang siap dikirim secara idempoten untuk `user_id`.

    Jika `job_type` diisi ('daily', 'recurring', 'weekly', atau 'monthly'), hanya mengevaluasi
    jenis tersebut; jika `None`, mengevaluasi keempat jenis sekaligus.
    Tidak akan mengirim ulang jika sudah tercatat di `sent_notifications` pada periode yang sama.
    """
    user = require_user(session, user_id)
    if job_type is not None and job_type not in NOTIFICATION_TYPES:
        raise ValidationError(
            f"Jenis notifikasi terjadwal tidak valid: '{job_type}'. "
            f"Pilihan: {', '.join(NOTIFICATION_TYPES)}."
        )

    rs, us = _ensure_synced_settings(session, user)
    target_date = resolve_date(user, as_of)
    items: list[ScheduledNotificationItem] = []

    # 1. Pengingat Harian (daily)
    if job_type in (None, NOTIFICATION_DAILY):
        daily_key = _default_period_key(NOTIFICATION_DAILY, target_date)
        if (
            us.is_daily_active
            and rs.is_daily_active
            and rs.last_daily_sent != target_date
            and not has_sent_notification(session, user_id, NOTIFICATION_DAILY, daily_key)
        ):
            d_payload = build_daily_reminder(session, user_id, as_of=target_date)
            mark_reminder_sent(
                session,
                user_id,
                NOTIFICATION_DAILY,
                sent_date=target_date,
                period_key=daily_key,
            )
            items.append(
                ScheduledNotificationItem(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    notification_type=NOTIFICATION_DAILY,
                    period_key=daily_key,
                    message=d_payload.message,
                )
            )

    # 2. Transaksi Rutin Jatuh Tempo (recurring)
    if job_type in (None, NOTIFICATION_RECURRING):
        rec_key = _default_period_key(NOTIFICATION_RECURRING, target_date)
        if us.is_recurring_active and not has_sent_notification(
            session, user_id, NOTIFICATION_RECURRING, rec_key
        ):
            r_payload = build_recurring_due_notification(session, user_id, as_of=target_date)
            if r_payload.has_due:
                record_sent_notification(session, user_id, NOTIFICATION_RECURRING, rec_key)
                items.append(
                    ScheduledNotificationItem(
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        notification_type=NOTIFICATION_RECURRING,
                        period_key=rec_key,
                        message=r_payload.message,
                    )
                )

    # 3. Ringkasan Mingguan (weekly - dikirim setiap hari Senin)
    if job_type in (None, NOTIFICATION_WEEKLY):
        weekly_key = _default_period_key(NOTIFICATION_WEEKLY, target_date)
        is_monday = target_date.weekday() == 0
        if (
            us.is_weekly_active
            and rs.is_weekly_active
            and is_monday
            and rs.last_weekly_sent != target_date
            and not has_sent_notification(session, user_id, NOTIFICATION_WEEKLY, weekly_key)
        ):
            w_payload = build_weekly_summary(session, user_id, as_of=target_date)
            mark_reminder_sent(
                session,
                user_id,
                NOTIFICATION_WEEKLY,
                sent_date=target_date,
                period_key=weekly_key,
            )
            items.append(
                ScheduledNotificationItem(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    notification_type=NOTIFICATION_WEEKLY,
                    period_key=weekly_key,
                    message=w_payload.message,
                )
            )

    # 4. Ringkasan Bulanan (monthly - dikirim setiap tanggal 1)
    if job_type in (None, NOTIFICATION_MONTHLY):
        monthly_key = _default_period_key(NOTIFICATION_MONTHLY, target_date)
        is_first_day = target_date.day == 1
        if (
            us.is_monthly_active
            and rs.is_monthly_active
            and is_first_day
            and rs.last_monthly_sent != target_date
            and not has_sent_notification(session, user_id, NOTIFICATION_MONTHLY, monthly_key)
        ):
            m_payload = build_monthly_summary(session, user_id, as_of=target_date)
            mark_reminder_sent(
                session,
                user_id,
                NOTIFICATION_MONTHLY,
                sent_date=target_date,
                period_key=monthly_key,
            )
            items.append(
                ScheduledNotificationItem(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    notification_type=NOTIFICATION_MONTHLY,
                    period_key=monthly_key,
                    message=m_payload.message,
                )
            )

    # 5. Peringatan Prediktif (Fitur V4 - dievaluasi jika job_type spesifik prediktif)
    if job_type in (NOTIFICATION_PREDICTIVE_BUDGET, NOTIFICATION_PREDICTIVE_RUNWAY):
        pred_items = collect_predictive_notifications(session, user_id, as_of=target_date)
        items.extend([p for p in pred_items if p.notification_type == job_type])

    return items


def collect_predictive_notifications(
    session: Session,
    user_id: int,
    as_of: date | None = None,
) -> list[ScheduledNotificationItem]:
    """Kumpulkan dan tandai peringatan prediktif yang siap dikirim idempoten per ambang."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)
    month_prefix = target_date.strftime("%Y-%m")

    from services.dashboard import get_runway_metrics
    from services.runway import evaluate_predictive_alerts

    metrics = get_runway_metrics(session, user_id, as_of=target_date)
    alerts = evaluate_predictive_alerts(metrics)

    items: list[ScheduledNotificationItem] = []
    for alert in alerts:
        period_key = f"{month_prefix}:{alert.threshold_code}"
        if not has_sent_notification(session, user_id, alert.notification_type, period_key):
            record_sent_notification(session, user_id, alert.notification_type, period_key)
            icon = "🚨" if alert.severity == "danger" else "⚠️"
            month_name = calendar.month_name[target_date.month]
            burn_str = format_rupiah(metrics.daily_burn_rate)
            proj_str = format_rupiah(metrics.projected_monthly_spend)
            lines = [
                f"{icon} *PERINGATAN PREDIKTIF KEUANGAN* ({month_name} {target_date.year})\n",
                f"*{alert.title}*",
                f"{alert.message}\n",
                f"• Laju Pengeluaran: `{burn_str}`/hari",
                f"• Proyeksi Belanja Akhir Bulan: `{proj_str}`",
            ]
            if metrics.recommended_daily_limit is not None:
                rec_str = format_rupiah(metrics.recommended_daily_limit)
                lines.append(f"• Batas Harian Disarankan: `{rec_str}`/hari")
            if metrics.balance_runway_days is not None:
                exp_date_str = (
                    metrics.balance_exhaustion_date.strftime("%d-%m-%Y")
                    if metrics.balance_exhaustion_date
                    else "-"
                )
                r_days = metrics.balance_runway_days
                lines.append(f"• Sisa Runway Saldo: {r_days} hari (habis: {exp_date_str})")

            items.append(
                ScheduledNotificationItem(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    notification_type=alert.notification_type,
                    period_key=period_key,
                    message="\n".join(lines),
                )
            )

    return items
