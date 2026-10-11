"""Titik masuk Personal Expense Tracker.

Fase 1: menyiapkan logging dan folder, menerapkan migrasi database (Alembic),
mengisi data awal, lalu menampilkan pesan awal. Menu CLI dan bot Telegram
akan ditambahkan di fase berikutnya.
"""

import logging
import sys
from datetime import datetime

import config
from database.engine import create_db_engine, create_session_factory, session_scope
from database.migrate import upgrade_to_head
from database.seed import ensure_default_user, seed_defaults

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Konfigurasi logging dasar ke stderr sesuai LOG_LEVEL di config."""
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)


def init_database() -> str:
    """Migrasi ke versi terbaru dan isi data awal. Mengembalikan nama pengguna aktif."""
    upgrade_to_head()
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            user = ensure_default_user(session)
            seed_defaults(session, user.id)
            return user.name
    finally:
        engine.dispose()


def print_welcome(user_name: str) -> None:
    """Tampilkan pesan awal beserta ringkasan konfigurasi (tanpa rahasia)."""
    today = datetime.now(config.TIMEZONE).strftime(config.DATE_FORMAT_DISPLAY)
    token_status = "terisi" if config.TELEGRAM_BOT_TOKEN else "belum diisi"

    print("=" * 50)
    print(f"  {config.APP_NAME} v{config.APP_VERSION}")
    print("=" * 50)
    print(f"Tanggal hari ini : {today} ({config.TIMEZONE_NAME})")
    print(f"Database         : {config.DB_PATH} (siap, migrasi terbaru)")
    print(f"Pengguna         : {user_name}")
    print(
        f"Ambang budget    : {config.BUDGET_WARNING_PERCENT}% / {config.BUDGET_EXCEEDED_PERCENT}%"
    )
    print(f"Token Telegram   : {token_status}")
    print(f"Whitelist ID     : {len(config.ALLOWED_TELEGRAM_IDS)} pengguna")
    print("-" * 50)
    print("Lapisan database siap. Fitur belum tersedia.")


def main() -> int:
    """Jalankan aplikasi. Mengembalikan kode keluar (0 = sukses)."""
    setup_logging()
    try:
        config.ensure_directories()
        user_name = init_database()
        print_welcome(user_name)
    except Exception:
        logger.exception("Gagal menjalankan aplikasi")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
