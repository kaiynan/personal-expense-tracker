"""Titik masuk Personal Expense Tracker.

Fase 0: hanya menyiapkan logging, folder, dan menampilkan pesan awal.
Menu CLI dan bot Telegram akan ditambahkan di fase berikutnya.
"""

import logging
import sys
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Konfigurasi logging dasar ke stderr sesuai LOG_LEVEL di config."""
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)


def print_welcome() -> None:
    """Tampilkan pesan awal beserta ringkasan konfigurasi (tanpa rahasia)."""
    today = datetime.now(config.TIMEZONE).strftime(config.DATE_FORMAT_DISPLAY)
    token_status = "terisi" if config.TELEGRAM_BOT_TOKEN else "belum diisi"

    print("=" * 50)
    print(f"  {config.APP_NAME} v{config.APP_VERSION}")
    print("=" * 50)
    print(f"Tanggal hari ini : {today} ({config.TIMEZONE_NAME})")
    print(f"Database         : {config.DB_PATH}")
    print(f"Ambang budget    : {config.BUDGET_WARNING_PERCENT}% / "
          f"{config.BUDGET_EXCEEDED_PERCENT}%")
    print(f"Token Telegram   : {token_status}")
    print(f"Whitelist ID     : {len(config.ALLOWED_TELEGRAM_IDS)} pengguna")
    print("-" * 50)
    print("Kerangka proyek siap. Fitur belum tersedia.")


def main() -> int:
    """Jalankan aplikasi. Mengembalikan kode keluar (0 = sukses)."""
    setup_logging()
    try:
        config.ensure_directories()
        print_welcome()
    except Exception:
        logger.exception("Gagal menjalankan aplikasi")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
