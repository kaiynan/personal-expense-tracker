"""Handler umum untuk Telegram Bot: /start, /help, /batal, dan navigasi menu."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from bot.auth import BLOCKED_MESSAGE, REJECTION_MESSAGE, whitelist_required
from bot.handlers.budgets import cmd_budget
from bot.handlers.quick import cmd_hari_ini, cmd_riwayat, cmd_saldo
from bot.handlers.reports import cmd_grafik, cmd_laporan
from bot.keyboards import main_reply_keyboard
from database import queries
from database.engine import session_scope
from database.seed import seed_defaults
from models import (
    ROLE_ADMIN,
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
)
from services import users as users_service
from utils.errors import AppError

logger = logging.getLogger(__name__)


def extract_command_args(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Ambil argumen perintah dari `context.args` atau teks pesan."""
    args = getattr(context, "args", None)
    if args:
        return [str(a) for a in args]
    if update.effective_message and update.effective_message.text:
        parts = update.effective_message.text.strip().split()
        if len(parts) > 1 and parts[0].startswith("/"):
            return parts[1:]
    return []


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sapaan awal & registrasi pengguna V3 (`/start` atau `/start <kode_undangan>`)."""
    if not update.effective_message:
        return
    user = update.effective_user
    if not user:
        await update.effective_message.reply_text(REJECTION_MESSAGE)
        return

    name = user.first_name or "Pengguna"
    args = extract_command_args(update, context)
    invite_code = args[0].strip() if args else None
    factory = context.application.bot_data["session_factory"]

    def _sync_start() -> tuple[str, int, str, str | None]:
        with session_scope(factory) as session:
            db_user, _is_new = queries.register_telegram_user(session, user.id, name=name)
            if db_user.status == USER_STATUS_BLOCKED:
                return "blocked", db_user.id, db_user.role, None

            if invite_code and db_user.status == USER_STATUS_PENDING:
                try:
                    db_user = users_service.redeem_invite(session, db_user.id, invite_code)
                except AppError as exc:
                    return "pending_error", db_user.id, db_user.role, str(exc)

            if db_user.status == USER_STATUS_ACTIVE:
                seed_defaults(session, db_user.id)
                return "active", db_user.id, db_user.role, None

            return "pending", db_user.id, db_user.role, None

    state, db_user_id, role, err_msg = await asyncio.to_thread(_sync_start)

    if state == "blocked":
        context.user_data.pop("db_user_id", None)
        await update.effective_message.reply_text(BLOCKED_MESSAGE)
        return

    if state == "pending_error":
        context.user_data.pop("db_user_id", None)
        await update.effective_message.reply_text(
            f"❌ Gagal mengaktifkan akun: {err_msg}\n\n"
            f"Akun Anda masih berstatus *pending* (ID Pengguna: `{db_user_id}`, "
            f"ID Telegram: `{user.id}`).\n"
            "Gunakan `/start <kode_undangan>` atau `/kode <kode_undangan>`, "
            "atau minta persetujuan admin.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if state == "pending":
        context.user_data.pop("db_user_id", None)
        pending_text = (
            f"Halo, *{name}*! 👋\n\n"
            "Pendaftaran Anda telah dicatat dengan status *pending* (menunggu aktivasi).\n"
            f"🆔 ID Pengguna: `{db_user_id}` | ID Telegram: `{user.id}`\n\n"
            "Untuk mengaktifkan akun Anda:\n"
            "1️⃣ Masukkan kode undangan dengan `/start <kode>` atau `/kode <kode>`\n"
            f"2️⃣ Atau minta admin menyetujui akun Anda dengan `/setujui {user.id}`"
        )
        await update.effective_message.reply_text(pending_text, parse_mode=ParseMode.MARKDOWN)
        return

    context.user_data["db_user_id"] = db_user_id
    admin_section = ""
    if role == ROLE_ADMIN:
        admin_section = (
            "\n🛡️ *Perintah Admin (V3):*\n"
            "• `/buat_undangan [kode]` - Buat kode undangan pengguna baru\n"
            "• `/setujui <id>` - Setujui pengguna berstatus pending\n"
            "• `/blokir <id>` - Blokir akses pengguna\n"
            "• `/pengguna` - Daftar pengguna & kode undangan\n"
        )

    text = (
        f"Halo, *{name}*! 👋\n\n"
        f"Selamat datang di *{config.APP_NAME}* Bot.\n"
        "Gunakan menu tombol di bawah atau perintah cepat untuk mengelola "
        "keuangan pribadi Anda.\n\n"
        "💡 *Perintah Cepat:*\n"
        "• `/saldo` - Cek saldo total dan per metode pembayaran\n"
        "• `/hari_ini` - Ringkasan dan daftar transaksi hari ini\n"
        "• `/riwayat` - 10 transaksi terakhir\n"
        "• `/tambah` - Catat pemasukan atau pengeluaran baru\n"
        "• `/transfer` - Pindahkan saldo antar metode pembayaran\n"
        "• `/laporan` - Laporan keuangan mingguan / bulanan\n"
        "• `/grafik` - Kirim 4 grafik visual keuangan ke chat\n"
        "• `/budget` - Pantau dan atur anggaran bulanan\n"
        "• `/pengingat` - Pengaturan pengingat harian & ringkasan otomatis\n"
        "• `/zona_waktu` - Pengaturan zona waktu (WIB/WITA/WIT)\n"
        "• `/ringkasan` - Ringkasan mingguan, bulanan, & insight keuangan\n"
        "• `/export` - Ekspor laporan & transaksi ke CSV, Excel, atau PDF\n"
        "• `/ekspor_data` - Unduh arsip lengkap data keuangan (.zip)\n"
        "• `/backup` - Cadangkan seluruh data keuangan ke file JSON\n"
        "• `/restore` - Pulihkan data dari file cadangan JSON atau ZIP\n"
        "• `/hapus_akun` - Hapus akun dan seluruh data keuangan secara permanen\n"
        "• `/alias` - Lihat dan kelola alias input cepat\n"
        "• `/konsistensi` - Cek konsistensi saldo agregat vs data mentah\n"
        "• `/help` - Panduan bantuan\n"
        "• `/batal` - Batalkan proses input yang sedang berjalan\n"
        f"{admin_section}\n"
        "🔒 *Privasi & Kendali Data:*\n"
        "• Data Anda diisolasi secara penuh dan tidak dapat dilihat oleh pengguna lain.\n"
        "• Anda dapat mengunduh seluruh data Anda kapan saja dengan `/ekspor_data`.\n"
        "• Anda memiliki kendali penuh untuk menghapus seluruh data Anda dengan `/hapus_akun`.\n\n"
        "⚡ *Input Cepat Telegram:*\n"
        "Ketik pesan seperti `makan 15000 gopay` atau `bensin 25k cash` "
        "untuk mencatat secara instan!"
    )
    await update.effective_message.reply_text(
        text, reply_markup=main_reply_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan bantuan panduan perintah bot."""
    if not update.effective_message:
        return
    text = (
        "📖 *PANDUAN PERINTAH BOT*\n\n"
        "1. *Transaksi & Saldo*\n"
        "• `/saldo` : Cek rincian saldo per rekening/dompet dan totalnya.\n"
        "• `/tambah` : Catat pemasukan atau pengeluaran dengan tombol bertahap.\n"
        "• `/transfer` : Pindahkan dana antar metode (mendukung biaya admin).\n"
        "• `/hari_ini` : Lihat transaksi yang dicatat pada hari ini.\n"
        "• `/riwayat` : Lihat 10 transaksi tersimpan terakhir.\n"
        "• `/alias` : Kelola alias kategori dan metode untuk input cepat.\n"
        "• `makan 15k gopay` : Input cepat langsung di obrolan.\n"
        "• `/konsistensi` : Cek konsistensi saldo (agregat vs data mentah).\n\n"
        "2. *Analisis, Laporan & Pengingat*\n"
        "• `/laporan` : Ringkasan pemasukan, pengeluaran, dan kategori mingguan/bulanan.\n"
        "• `/grafik` : Mengirimkan 4 chart PNG (kategori, arus kas, tren saldo).\n"
        "• `/budget` : Evaluasi peringatan batas anggaran (80% / 100%).\n"
        "• `/pengingat` : Atur pengingat harian & ringkasan otomatis.\n"
        "• `/waktu_pengingat <HH:MM>` : Ubah jam pengingat harian.\n"
        "• `/zona_waktu [WIB|WITA|WIT]` : Atur zona waktu pengguna.\n"
        "• `/ringkasan` : Ringkasan mingguan/bulanan beserta insight keuangan.\n\n"
        "3. *Ekspor, Cadangan & Kendali Data*\n"
        "• `/export` : Ekspor transaksi & laporan ke CSV, Excel (.xlsx), atau PDF (.pdf).\n"
        "• `/ekspor_data` : Ekspor arsip ZIP lengkap (JSON + CSV) milik pengguna.\n"
        "• `/backup` : Ekspor dan unduh file cadangan data keuangan (.json).\n"
        "• `/restore` : Pulihkan data dari cadangan di server atau unggah file .json/.zip.\n"
        "• `/hapus_akun` : Hapus akun dan seluruh data pengguna secara permanen.\n"
        "• `/batal` : Batalkan pengisian form yang sedang berlangsung."
    )
    await update.effective_message.reply_text(
        text, reply_markup=main_reply_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


@whitelist_required
async def cmd_hapus_akun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hapus akun pengguna dan seluruh data keuangannya secara permanen."""
    if not update.effective_message:
        return

    args = extract_command_args(update, context)
    token = args[0].strip().upper() if args else ""

    if token != "KONFIRMASI":
        warning_text = (
            "⚠️ *PERINGATAN HAPUS AKUN*\n\n"
            "Tindakan ini akan menghapus akun Anda dan *SELURUH* data keuangan Anda "
            "(transaksi, kategori, rekening/metode pembayaran, anggaran, pengingat, "
            "saldo awal, dan riwayat audit) secara *permanen*!\n\n"
            "Data yang telah dihapus *TIDAK DAPAT* dipulihkan kembali.\n\n"
            "Jika Anda benar-benar yakin ingin menghapus akun dan semua data Anda, silakan ketik:\n"
            "`/hapus_akun KONFIRMASI`"
        )
        await update.effective_message.reply_text(warning_text, parse_mode=ParseMode.MARKDOWN)
        return

    factory = context.application.bot_data["session_factory"]
    user_id = context.user_data.get("db_user_id")
    if not user_id:
        return

    def _sync_delete() -> None:
        with session_scope(factory) as session:
            users_service.delete_user_account(session, user_id)

    try:
        await asyncio.to_thread(_sync_delete)
        context.user_data.clear()
        success_text = (
            "✅ *AKUN BERHASIL DIHAPUS*\n\n"
            "Seluruh data dan akun Anda telah dihapus secara permanen dari sistem.\n"
            "Terima kasih telah menggunakan Personal Expense Tracker."
        )
        await update.effective_message.reply_text(success_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Gagal menghapus akun: %s", e)
        await update.effective_message.reply_text(
            f"❌ Gagal menghapus akun: {e}", parse_mode=ParseMode.MARKDOWN
        )


@whitelist_required
async def cmd_batal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Batalkan tindakan apapun dan kembali ke menu utama."""
    if not update.effective_message:
        return
    context.user_data.pop("new_tx", None)
    context.user_data.pop("new_trf", None)
    context.user_data.pop("budget_setup", None)
    context.user_data.pop("pending_restore", None)
    await update.effective_message.reply_text(
        "Tindakan telah dibatalkan.", reply_markup=main_reply_keyboard()
    )


@whitelist_required
async def handle_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Router tombol reply keyboard utama."""
    if not update.effective_message or not update.effective_message.text:
        return
    text = update.effective_message.text.strip()

    if text == "💰 Cek Saldo":
        await cmd_saldo(update, context)
    elif text == "📅 Hari Ini":
        await cmd_hari_ini(update, context)
    elif text == "📜 Riwayat":
        await cmd_riwayat(update, context)
    elif text == "📊 Laporan":
        await cmd_laporan(update, context)
    elif text == "📈 Grafik Keuangan":
        await cmd_grafik(update, context)
    elif text == "🎯 Budget":
        await cmd_budget(update, context)
    elif text == "❓ Bantuan":
        await cmd_help(update, context)
    else:
        from bot.handlers.aliases import handle_quick_input

        await handle_quick_input(update, context, text)
