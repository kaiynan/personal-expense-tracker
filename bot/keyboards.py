"""Keyboard dan tombol interaktif untuk Telegram Bot."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Menu tombol persisten utama di bagian bawah layar obrolan."""
    keyboard = [
        ["+ Tambah Transaksi", "🔁 Transfer"],
        ["💰 Cek Saldo", "📅 Hari Ini"],
        ["📜 Riwayat", "📊 Laporan"],
        ["📈 Grafik Keuangan", "🎯 Budget"],
        ["❓ Bantuan"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def transaction_type_keyboard() -> InlineKeyboardMarkup:
    """Pilihan jenis transaksi baru."""
    buttons = [
        [
            InlineKeyboardButton("🟢 Pemasukan", callback_data="type_income"),
            InlineKeyboardButton("🔴 Pengeluaran", callback_data="type_expense"),
        ],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(buttons)


def categories_inline_keyboard(categories: Sequence[Any]) -> InlineKeyboardMarkup:
    """Daftar tombol pilihan kategori."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for cat in categories:
        row.append(InlineKeyboardButton(cat.name, callback_data=f"cat_{cat.id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="cancel_action")])
    return InlineKeyboardMarkup(buttons)


def methods_inline_keyboard(
    methods: Sequence[Any], exclude_id: int | None = None
) -> InlineKeyboardMarkup:
    """Daftar tombol pilihan metode pembayaran."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for m in methods:
        mid = getattr(m, "id", None) or getattr(m, "method_id", None)
        if exclude_id is not None and mid == exclude_id:
            continue
        row.append(InlineKeyboardButton(m.name, callback_data=f"met_{mid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="cancel_action")])
    return InlineKeyboardMarkup(buttons)


def today_date_keyboard() -> InlineKeyboardMarkup:
    """Tombol cepat untuk memilih tanggal hari ini atau batal."""
    buttons = [
        [InlineKeyboardButton("📅 Gunakan Hari Ini", callback_data="date_today")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(buttons)


def skip_note_keyboard() -> InlineKeyboardMarkup:
    """Tombol cepat untuk melewati pengisian keterangan."""
    buttons = [
        [InlineKeyboardButton("⏩ Lewati Keterangan", callback_data="note_skip")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(buttons)


def zero_admin_fee_keyboard() -> InlineKeyboardMarkup:
    """Tombol cepat biaya admin nol untuk transfer."""
    buttons = [
        [InlineKeyboardButton("0 (Tanpa Biaya Admin)", callback_data="fee_zero")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(buttons)


def confirmation_keyboard(confirm_callback: str = "confirm_save") -> InlineKeyboardMarkup:
    """Tombol konfirmasi simpan atau batal."""
    buttons = [
        [
            InlineKeyboardButton("✅ Simpan", callback_data=confirm_callback),
            InlineKeyboardButton("❌ Batal", callback_data="cancel_action"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def reports_menu_keyboard() -> InlineKeyboardMarkup:
    """Pilihan rentang waktu laporan keuangan."""
    buttons = [
        [
            InlineKeyboardButton("📅 Laporan Hari Ini", callback_data="rep_daily"),
            InlineKeyboardButton("📊 Laporan Minggu Ini", callback_data="rep_weekly"),
        ],
        [
            InlineKeyboardButton("🗓️ Laporan Bulan Ini", callback_data="rep_monthly"),
            InlineKeyboardButton("⚖️ Perbandingan Periode", callback_data="rep_compare"),
        ],
        [
            InlineKeyboardButton("📈 Statistik Keuangan", callback_data="rep_stats"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def budget_action_keyboard() -> InlineKeyboardMarkup:
    """Pilihan aksi pengaturan anggaran."""
    buttons = [
        [InlineKeyboardButton("📝 Atur Budget Total Bulanan", callback_data="bgt_set_total")],
        [InlineKeyboardButton("📂 Atur Budget per Kategori", callback_data="bgt_set_category")],
        [
            InlineKeyboardButton("📅 Budget Total Mingguan", callback_data="bgt_set_weekly_total"),
            InlineKeyboardButton("🗂️ Budget Kat. Mingguan", callback_data="bgt_set_weekly_category"),
        ],
        [InlineKeyboardButton("🔄 Rollover Sisa Bulan Lalu", callback_data="bgt_rollover")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(buttons)


def quick_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Tombol konfirmasi untuk input cepat Telegram."""
    buttons = [
        [
            InlineKeyboardButton("✅ Simpan", callback_data="quick_confirm"),
            InlineKeyboardButton("❌ Batal", callback_data="quick_cancel"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def timezone_selection_keyboard(current_tz: str | None = None) -> InlineKeyboardMarkup:
    """Tombol inline untuk memilih zona waktu pengguna (WIB / WITA / WIT)."""
    wib_mark = "✅ " if current_tz == "Asia/Jakarta" else ""
    wita_mark = "✅ " if current_tz == "Asia/Makassar" else ""
    wit_mark = "✅ " if current_tz == "Asia/Jayapura" else ""
    buttons = [
        [
            InlineKeyboardButton(f"{wib_mark}🕒 WIB (UTC+7)", callback_data="rem_tz_WIB"),
            InlineKeyboardButton(f"{wita_mark}🕓 WITA (UTC+8)", callback_data="rem_tz_WITA"),
            InlineKeyboardButton(f"{wit_mark}🕔 WIT (UTC+9)", callback_data="rem_tz_WIT"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def reminder_settings_keyboard(setting: Any) -> InlineKeyboardMarkup:
    """Tombol interaktif pengaturan pengingat harian, transaksi rutin, ringkasan, dan zona waktu."""
    d_label = "🔕 Matikan Harian" if setting.is_daily_active else "🔔 Aktifkan Harian"
    w_label = "🔕 Matikan Mingguan" if setting.is_weekly_active else "🔔 Aktifkan Mingguan"
    m_label = "🔕 Matikan Bulanan" if setting.is_monthly_active else "🔔 Aktifkan Bulanan"
    rec_active = getattr(setting, "is_recurring_active", True)
    r_label = "🔕 Matikan Trx Rutin" if rec_active else "🔔 Aktifkan Trx Rutin"
    cur_tz = getattr(setting, "timezone", None)
    wib_mark = "✅ " if cur_tz == "Asia/Jakarta" else ""
    wita_mark = "✅ " if cur_tz == "Asia/Makassar" else ""
    wit_mark = "✅ " if cur_tz == "Asia/Jayapura" else ""

    buttons = [
        [
            InlineKeyboardButton(d_label, callback_data="rem_toggle_daily"),
            InlineKeyboardButton(w_label, callback_data="rem_toggle_weekly"),
        ],
        [
            InlineKeyboardButton(m_label, callback_data="rem_toggle_monthly"),
            InlineKeyboardButton(r_label, callback_data="rem_toggle_recurring"),
        ],
        [
            InlineKeyboardButton(f"{wib_mark}WIB", callback_data="rem_tz_WIB"),
            InlineKeyboardButton(f"{wita_mark}WITA", callback_data="rem_tz_WITA"),
            InlineKeyboardButton(f"{wit_mark}WIT", callback_data="rem_tz_WIT"),
        ],
        [
            InlineKeyboardButton("👁️ Preview Harian", callback_data="rem_preview_daily"),
            InlineKeyboardButton("📊 Ringkasan Mingguan", callback_data="rem_preview_weekly"),
        ],
        [
            InlineKeyboardButton(
                "🗓️ Ringkasan & Insight Bulanan", callback_data="rem_preview_monthly"
            ),
            InlineKeyboardButton("⏰ Cek Trx Rutin", callback_data="rem_preview_recurring"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def restore_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Tombol konfirmasi untuk eksekusi pemulihan data (restore)."""
    buttons = [
        [
            InlineKeyboardButton("⚠️ Ya, Pulihkan Data", callback_data="bkp_confirm"),
            InlineKeyboardButton("❌ Batal", callback_data="bkp_cancel"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def backup_list_keyboard(backups: Sequence[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Daftar tombol pilihan file cadangan untuk dipulihkan."""
    buttons: list[list[InlineKeyboardButton]] = []
    for idx, b in enumerate(backups[:5]):
        label = f"📂 {b['filename']} ({b.get('transactions_count', 0)} tx)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"bkp_sel_{idx}")])
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="bkp_cancel")])
    return InlineKeyboardMarkup(buttons)


def export_menu_keyboard() -> InlineKeyboardMarkup:
    """Tombol pilihan format dan periode ekspor data (CSV, Excel, PDF)."""
    buttons = [
        [
            InlineKeyboardButton("📄 CSV (Bulan Ini)", callback_data="exp_csv_month"),
            InlineKeyboardButton("📊 Excel (Bulan Ini)", callback_data="exp_xlsx_month"),
            InlineKeyboardButton("📑 PDF (Bulan Ini)", callback_data="exp_pdf_month"),
        ],
        [
            InlineKeyboardButton("📄 CSV (Minggu Ini)", callback_data="exp_csv_week"),
            InlineKeyboardButton("📊 Excel (Minggu Ini)", callback_data="exp_xlsx_week"),
            InlineKeyboardButton("📑 PDF (Minggu Ini)", callback_data="exp_pdf_week"),
        ],
        [
            InlineKeyboardButton("📄 CSV (Semua)", callback_data="exp_csv_all"),
            InlineKeyboardButton("📊 Excel (Semua)", callback_data="exp_xlsx_all"),
            InlineKeyboardButton("📑 PDF (Semua)", callback_data="exp_pdf_all"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)
