"""Skrip dan modul pencadangan berkala dengan kebijakan retensi file cadangan."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

import config

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 14
DEFAULT_MIN_KEEP = 5


def perform_scheduled_backup(
    destination_dir: Path | None = None,
) -> Path | None:
    """Buat salinan database SQLite online menggunakan SQLite Backup API yang konsisten."""
    config.ensure_directories()
    dest_dir = destination_dir or config.BACKUPS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(config.DB_PATH)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        logger.info("Database kosong atau belum ada. Melewati pencadangan.")
        return None

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = dest_dir / f"full_backup_{timestamp}.db"

    # Gunakan SQLite online backup API agar aman dijalankan saat bot sedang berjalan
    try:
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dest_conn = sqlite3.connect(backup_file)
        with dest_conn:
            src_conn.backup(dest_conn)
        dest_conn.close()
        src_conn.close()
    except Exception:
        # Fallback ke copy jika URI readonly tidak didukung
        shutil.copy2(db_path, backup_file)

    logger.info(
        "Pencadangan berhasil dibuat: %s (%d bytes)",
        backup_file,
        backup_file.stat().st_size,
    )
    return backup_file


def prune_backups(
    retention_days: int = DEFAULT_RETENTION_DAYS,
    min_keep: int = DEFAULT_MIN_KEEP,
    directory: Path | None = None,
) -> list[Path]:
    """Hapus file cadangan yang lebih tua dari `retention_days`,
    sambil menjaga minimal `min_keep` file cadangan terbaru.
    """
    dest_dir = directory or config.BACKUPS_DIR
    if not dest_dir.is_dir():
        return []

    patterns = ("full_backup_*.db", "backup_*.zip", "backup_*.json", "pre_migration_*.db")
    all_files: list[Path] = []
    for pattern in patterns:
        all_files.extend(dest_dir.glob(pattern))

    # Hapus duplikasi dan urutkan dari yang terbaru ke terlama
    unique_files = sorted(set(all_files), key=lambda p: p.stat().st_mtime, reverse=True)

    if len(unique_files) <= min_keep:
        return []

    cutoff_time = dt.datetime.now() - dt.timedelta(days=retention_days)
    cutoff_timestamp = cutoff_time.timestamp()

    # Lindungi min_keep file pertama dari penghapusan
    candidates = unique_files[min_keep:]
    deleted: list[Path] = []

    for file_path in candidates:
        try:
            if file_path.stat().st_mtime < cutoff_timestamp:
                file_path.unlink(missing_ok=True)
                deleted.append(file_path)
                logger.info(
                    "Menghapus cadangan kedaluwarsa (%d hari): %s",
                    retention_days,
                    file_path.name,
                )
        except Exception as exc:
            logger.warning("Gagal menghapus file cadangan %s: %s", file_path, exc)

    return deleted


def run_backup_and_retention(
    retention_days: int = DEFAULT_RETENTION_DAYS,
    min_keep: int = DEFAULT_MIN_KEEP,
) -> tuple[Path | None, list[Path]]:
    """Jalankan siklus lengkap: buat backup terjadwal lalu pangkas file lama sesuai retensi."""
    backup_path = perform_scheduled_backup()
    pruned = prune_backups(retention_days=retention_days, min_keep=min_keep)
    return backup_path, pruned


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        created, deleted = run_backup_and_retention()
        if created:
            print(f"[OK] Backup selesai: {created}")
        if deleted:
            print(f"[CLEAN] {len(deleted)} file cadangan lama dipangkas sesuai retensi.")
        else:
            print("[INFO] Tidak ada file cadangan yang perlu dipangkas.")
        sys.exit(0)
    except Exception as exc:
        print(f"[ERROR] Terjadi kesalahan: {exc}", file=sys.stderr)
        sys.exit(1)
