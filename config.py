"""Konfigurasi terpusat Personal Expense Tracker.

Semua pengaturan aplikasi dibaca dari modul ini. Nilai rahasia (token bot,
whitelist) diambil dari file `.env`; sisanya berupa konstanta.

Modul ini sengaja tidak punya efek samping saat di-import (tidak membuat
folder/file). Pembuatan folder dilakukan lewat `ensure_directories()`.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# --- Path dasar ---------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent

# Muat .env dari root proyek (jika ada). Variabel lingkungan sistem
# yang sudah terisi tidak ditimpa.
load_dotenv(BASE_DIR / ".env")

# --- Aplikasi -----------------------------------------------------------------
APP_NAME: str = "Personal Expense Tracker"
APP_VERSION: str = "0.1.0"

# --- Database & folder output -------------------------------------------------
DATA_DIR: Path = BASE_DIR / "data"
REPORTS_DIR: Path = BASE_DIR / "reports_output"
BACKUPS_DIR: Path = BASE_DIR / "backups"

# DB_PATH bisa dioverride lewat .env (relatif terhadap root proyek).
DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "data/expense_tracker.db")

# URL SQLAlchemy/Alembic. SQLite dulu; kode tidak memakai fitur khusus SQLite
# sehingga bagian ini cukup diganti saat pindah ke PostgreSQL.
DATABASE_URL: str = f"sqlite:///{DB_PATH.as_posix()}"

# --- Zona waktu & format tanggal ----------------------------------------------
TIMEZONE_NAME: str = "Asia/Jakarta"
TIMEZONE: ZoneInfo = ZoneInfo(TIMEZONE_NAME)

DATE_FORMAT_DB: str = "%Y-%m-%d"  # disimpan di database (ISO)
DATE_FORMAT_DISPLAY: str = "%d-%m-%Y"  # input/tampilan ke pengguna
WEEK_START: int = 0  # 0 = Senin (sesuai datetime.weekday())

# --- Budget -------------------------------------------------------------------
# Ambang dalam persen (integer) dari nominal budget.
BUDGET_WARNING_PERCENT: int = 80  # >= nilai ini: "mendekati batas"
BUDGET_EXCEEDED_PERCENT: int = 100  # >= nilai ini: "melewati batas"

# --- Validasi input -------------------------------------------------------------
# Nominal maksimum satu transaksi / saldo yang boleh dimasukkan (rupiah, integer).
# Mencegah salah ketik (mis. kelebihan beberapa nol) masuk ke database.
MAX_AMOUNT: int = 1_000_000_000_000  # Rp1 triliun
# Panjang maksimum keterangan; tidak boleh melebihi panjang kolom `transactions.note`.
MAX_NOTE_LENGTH: int = 255
# Panjang maksimum nama kategori; tidak boleh melebihi panjang kolom `categories.name`.
MAX_CATEGORY_NAME_LENGTH: int = 50
# Panjang maksimum nama transaksi rutin; tidak boleh melebihi kolom `recurring.name`.
MAX_RECURRING_NAME_LENGTH: int = 100

# --- Batas Transaksi Harian & Rate Limit (V3) ---------------------------------
# Batas jumlah transaksi per hari yang dapat dicatat oleh satu pengguna.
MAX_DAILY_TRANSACTIONS_PER_USER: int = int(os.getenv("MAX_DAILY_TRANSACTIONS_PER_USER", "100"))
# Batas jumlah request/perintah bot per menit per pengguna.
RATE_LIMIT_REQUESTS_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "30"))

# --- Telegram -----------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _parse_id_list(raw: str) -> frozenset[int]:
    """Ubah string "123, 456" menjadi frozenset {123, 456}.

    Melempar ValueError dengan pesan jelas jika ada entri bukan angka,
    supaya salah ketik di .env tidak diam-diam membuka akses bot.
    """
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ValueError(
                f"ALLOWED_TELEGRAM_IDS berisi nilai tidak valid: {part!r}. "
                "Gunakan angka dipisah koma, contoh: 123456789,987654321"
            )
        ids.add(int(part))
    return frozenset(ids)


# Whitelist user ID Telegram. Kosong = tidak ada yang diizinkan.
ALLOWED_TELEGRAM_IDS: frozenset[int] = _parse_id_list(os.getenv("ALLOWED_TELEGRAM_IDS", ""))

# --- Logging ------------------------------------------------------------------
LOGS_DIR: Path = BASE_DIR / "logs"
LOG_FILE: Path = LOGS_DIR / "app.log"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# --- Keamanan & Web Dashboard (Fase W) ---------------------------------------
SECRET_KEY: str = os.getenv(
    "SECRET_KEY", "personal-expense-tracker-secret-key-change-in-production"
)
WEB_OTP_EXPIRY_MINUTES: int = int(os.getenv("WEB_OTP_EXPIRY_MINUTES", "5"))
WEB_OTP_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("WEB_OTP_RATE_LIMIT_PER_MINUTE", "3"))


def ensure_directories() -> None:
    """Buat folder data, logs, output laporan, dan backups jika belum ada."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
