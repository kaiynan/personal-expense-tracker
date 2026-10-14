"""Titik masuk Personal Expense Tracker.

Inisialisasi konfigurasi, logging, dan migrasi database, lalu menjalankan menu CLI.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Any

import config
from bot import run_bot
from cli import run_cli
from database.engine import create_db_engine, create_session_factory, session_scope
from database.migrate import upgrade_to_head
from database.seed import ensure_default_user, seed_defaults

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Konfigurasi logging ke console dan file logs/app.log (tanpa data sensitif)."""
    config.ensure_directories()
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format=config.LOG_FORMAT,
        handlers=handlers,
        force=True,
    )


def init_database() -> tuple[Any, int, str]:
    """Migrasi ke versi terbaru dan isi data awal.

    Mengembalikan (engine, user_id, user_name).
    """
    upgrade_to_head()
    engine = create_db_engine()
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        user = ensure_default_user(session)
        seed_defaults(session, user.id)
        user_id = user.id
        user_name = user.name
    return engine, user_id, user_name


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


def main(argv: list[str] | None = None) -> int:
    """Jalankan aplikasi. Mengembalikan kode keluar (0 = sukses)."""
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--bot", action="store_true", help="Jalankan antarmuka Telegram Bot saja")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Jalankan Web Dashboard (+ Telegram Bot bersamaan dalam satu proses)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port server web (default: $PORT atau 8000)",
    )
    parser.add_argument(
        "--no-bot",
        action="store_true",
        help="Nonaktifkan polling bot di Web Dashboard (berguna jika bot sudah aktif di cloud)",
    )
    args = parser.parse_args(argv)

    if args.no_bot:
        os.environ["ENABLE_BOT_POLLING"] = "false"

    setup_logging()
    try:
        config.ensure_directories()
        engine, user_id, user_name = init_database()
        try:
            if args.web:
                import uvicorn

                port = args.port or int(os.environ.get("PORT", "8000"))
                logger.info("Menjalankan %s Web Dashboard di port %s...", config.APP_NAME, port)
                engine.dispose()
                uvicorn.run("web.app:app", host="0.0.0.0", port=port, log_level="info")
                return 0

            factory = create_session_factory(engine)
            if args.bot:
                logger.info("Menjalankan %s Telegram Bot...", config.APP_NAME)
                run_bot(factory)
            else:
                print_welcome(user_name)
                run_cli(factory, user_id)
        finally:
            if not args.web:
                engine.dispose()
    except Exception:
        logger.exception("Gagal menjalankan aplikasi")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
