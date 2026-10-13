"""Skrip pencadangan otomatis database SQLite sebelum eksekusi migrasi Alembic."""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import config


def run_pre_migration_backup(max_keep: int = 10) -> Path | None:
    """Buat salinan database SQLite sebelum migrasi dijalankan.

    Jika database belum ada (instalasi baru), fungsi mengembalikan None.
    Hanya mempertahankan `max_keep` file pencadangan pre-migration terakhir.
    """
    config.ensure_directories()
    db_file = Path(config.DB_PATH)

    if not db_file.is_file() or db_file.stat().st_size == 0:
        print(f"[INFO] Database belum dibuat atau kosong ({db_file}). Melewati backup pre-migrasi.")
        return None

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"pre_migration_{timestamp}.db"
    dest_path = config.BACKUPS_DIR / backup_filename

    shutil.copy2(db_file, dest_path)
    print(f"[OK] Backup pre-migrasi dibuat: {dest_path} ({dest_path.stat().st_size} bytes)")

    # Pangkas cadangan pre-migrasi lama jika melebihi batas simpan
    prune_old_pre_migration_backups(max_keep=max_keep)
    return dest_path


def prune_old_pre_migration_backups(max_keep: int = 10) -> int:
    """Hapus file pre_migration_*.db lama yang melebihi batas `max_keep`."""
    if not config.BACKUPS_DIR.is_dir():
        return 0

    backups = sorted(
        config.BACKUPS_DIR.glob("pre_migration_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    if len(backups) > max_keep:
        to_delete = backups[max_keep:]
        for f in to_delete:
            try:
                f.unlink(missing_ok=True)
                removed += 1
            except Exception as exc:
                print(f"[WARN] Gagal menghapus file backup lama {f}: {exc}", file=sys.stderr)

    if removed > 0:
        print(f"[CLEAN] Membersihkan {removed} file backup pre-migrasi lama.")
    return removed


if __name__ == "__main__":
    try:
        run_pre_migration_backup()
        sys.exit(0)
    except Exception as exc:
        print(f"[ERROR] Gagal melakukan backup pre-migrasi: {exc}", file=sys.stderr)
        sys.exit(1)
