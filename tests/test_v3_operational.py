"""Pengujian fitur operasional V3:
- Healthcheck container
- Backup otomatis sebelum migrasi dan pemangkasan
- Backup berkala dengan retensi
- Pengujian skrip restore cadangan
- Perintah /status admin
- Notifikasi error ke administrator via Telegram
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update
from telegram import User as TgUser
from telegram.ext import ContextTypes

import config
from bot.app import get_admin_telegram_ids, global_error_handler
from bot.handlers.admin import cmd_status
from bot.handlers.reminders import scheduled_backup_retention_job
from database.engine import create_db_engine, create_session_factory, session_scope
from models import Base, User
from scripts.backup_pre_migration import (
    prune_old_pre_migration_backups,
    run_pre_migration_backup,
)
from scripts.backup_retention import (
    perform_scheduled_backup,
    prune_backups,
)
from scripts.healthcheck import run_healthcheck
from scripts.test_restore import run_restore_test, verify_sqlite_db_integrity


@pytest.fixture
def operational_env():
    """Siapkan environment file DB dan backups terisolasi."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "test_ops.db"
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)

        engine = create_db_engine(f"sqlite:///{db_file.as_posix()}")
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)

        with session_scope(factory) as session:
            admin = User(telegram_id=111111, name="Admin User", role="admin", status="active")
            regular = User(telegram_id=222222, name="Regular User", role="user", status="active")
            session.add_all([admin, regular])

        old_db_path = config.DB_PATH
        old_backups_dir = config.BACKUPS_DIR

        config.DB_PATH = db_file
        config.BACKUPS_DIR = backups_dir

        try:
            yield {
                "db_file": db_file,
                "backups_dir": backups_dir,
                "factory": factory,
                "engine": engine,
            }
        finally:
            engine.dispose()
            config.DB_PATH = old_db_path
            config.BACKUPS_DIR = old_backups_dir


def test_healthcheck_script(operational_env):
    """Healthcheck mengembalikan True saat koneksi database normal."""
    assert run_healthcheck() is True

    # Jika URL database sengaja dirusak
    with patch("config.DATABASE_URL", "sqlite:////non_existent_dir_xyz/test.db"):
        assert run_healthcheck() is False


def test_pre_migration_backup_and_pruning(operational_env):
    """Backup pra-migrasi membuat salinan database dan memangkas file melebihi max_keep."""
    backups_dir = operational_env["backups_dir"]

    # Buat backup pra-migrasi
    res = run_pre_migration_backup(max_keep=3)
    assert res is not None
    assert res.is_file()
    assert "pre_migration_" in res.name

    # Buat beberapa file dummy tambahan untuk menguji pemangkasan
    for i in range(5):
        dummy = backups_dir / f"pre_migration_dummy_{i}.db"
        dummy.write_text("dummy", encoding="utf-8")

    assert len(list(backups_dir.glob("pre_migration_*.db"))) >= 6

    pruned = prune_old_pre_migration_backups(max_keep=3)
    assert pruned > 0
    assert len(list(backups_dir.glob("pre_migration_*.db"))) == 3


def test_scheduled_backup_and_retention(operational_env):
    """Pencadangan berkala online menghasilkan file valid dan mematuhi retensi."""
    backups_dir = operational_env["backups_dir"]

    created = perform_scheduled_backup(destination_dir=backups_dir)
    assert created is not None
    assert created.is_file()
    assert "full_backup_" in created.name

    # Verifikasi integritas cadangan yang dibuat
    is_ok, msg, counts = verify_sqlite_db_integrity(created)
    assert is_ok is True
    assert "PRAGMA integrity_check: ok" in msg
    assert "users" in counts

    # Buat file backup dengan tanggal masa lalu (>14 hari yang lalu)
    old_file = backups_dir / "full_backup_20200101_000000.db"
    old_file.write_text("old", encoding="utf-8")
    old_timestamp = (dt.datetime.now() - dt.timedelta(days=30)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    # Pastikan file lama dipangkas jika jumlah file melampaui min_keep (buat dummy files baru)
    for i in range(5):
        f = backups_dir / f"full_backup_dummy_{i}.db"
        f.write_text("data", encoding="utf-8")

    deleted = prune_backups(retention_days=14, min_keep=3, directory=backups_dir)
    assert old_file in deleted
    assert not old_file.exists()


def test_restore_test_script(operational_env):
    """Skrip test restore memvalidasi integritas file cadangan dan mengembalikan True."""
    created = perform_scheduled_backup(destination_dir=operational_env["backups_dir"])
    assert run_restore_test(created) is True

    # Jika file rusak
    corrupt_file = operational_env["backups_dir"] / "corrupt.db"
    corrupt_file.write_bytes(b"not an sqlite database file at all")
    assert run_restore_test(corrupt_file) is False


def test_cmd_status_displays_comprehensive_diagnostics(operational_env):
    """Perintah /status menampilkan metrik server, SQLite, pengguna, dan backup untuk admin."""
    factory = operational_env["factory"]

    app = MagicMock()
    app.bot_data = {"session_factory": factory}
    app.job_queue = MagicMock()
    app.job_queue.jobs.return_value = [MagicMock(), MagicMock()]

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.application = app
    context.user_data = {"db_user_id": 1}  # Admin

    update = MagicMock(spec=Update)
    message = AsyncMock(spec=Message)
    user = MagicMock(spec=TgUser)
    user.id = 111111
    user.first_name = "Admin User"
    update.effective_message = message
    update.effective_user = user

    asyncio.run(cmd_status(update, context))

    message.reply_text.assert_awaited_once()
    reply_text = message.reply_text.call_args[0][0]
    assert "STATUS SISTEM & OPERASIONAL" in reply_text
    assert "Database SQLite" in reply_text
    assert "Integritas   : `ok`" in reply_text
    assert "WAL" in reply_text
    assert "Pengguna" in reply_text
    assert "Job Aktif Terdaftar : `2` job" in reply_text


def test_get_admin_telegram_ids(operational_env):
    """get_admin_telegram_ids mengumpulkan ID admin dari konfigurasi dan database."""
    factory = operational_env["factory"]
    with patch("config.ALLOWED_TELEGRAM_IDS", ["999999"]):
        admins = get_admin_telegram_ids(factory)
        assert 999999 in admins
        assert 111111 in admins  # admin di DB
        assert 222222 not in admins  # user biasa di DB


def test_global_error_handler_alerts_admins(operational_env):
    """global_error_handler mengirim notifikasi traceback ke seluruh admin Telegram."""
    factory = operational_env["factory"]

    app = MagicMock()
    app.bot_data = {"session_factory": factory}

    bot = AsyncMock()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.application = app
    context.bot = bot
    context.error = ValueError("Koneksi eksternal terputus")

    update = MagicMock(spec=Update)
    message = AsyncMock(spec=Message)
    user = MagicMock(spec=TgUser)
    user.id = 555
    user.first_name = "Budi"
    user.username = "budi_test"

    message.text = "/transaksi makan"
    update.effective_message = message
    update.effective_user = user

    with patch("config.ALLOWED_TELEGRAM_IDS", ["111111"]):
        asyncio.run(global_error_handler(update, context))

    # Pesan ramah ke pengguna
    message.reply_text.assert_awaited_once()
    assert "Administrator telah menerima laporan" in message.reply_text.call_args[0][0]

    # Pesan alert ke admin
    bot.send_message.assert_awaited()
    admin_calls = bot.send_message.call_args_list
    assert len(admin_calls) >= 1
    sent_alert = admin_calls[0].kwargs["text"]
    assert "NOTIFIKASI ERROR SISTEM" in sent_alert
    assert "ValueError" in sent_alert
    assert "Koneksi eksternal terputus" in sent_alert
    assert "Budi" in sent_alert


def test_scheduled_backup_retention_job(operational_env):
    """JobQueue scheduled_backup_retention_job mengeksekusi siklus backup & retensi."""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    with patch("scripts.backup_retention.run_backup_and_retention") as mock_run:
        mock_run.return_value = (Path("full_backup_test.db"), [])
        asyncio.run(scheduled_backup_retention_job(context))
        mock_run.assert_called_once()
