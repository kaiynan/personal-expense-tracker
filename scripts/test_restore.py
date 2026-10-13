"""Skrip verifikasi dan pengujian pemulihan (restore test) cadangan database.

Memverifikasi integritas file cadangan (.db, .zip, .json) ke dalam database terisolasi
tanpa mengubah atau membahayakan database produksi/live.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import config
from database.engine import create_db_engine
from scripts.backup_retention import perform_scheduled_backup


def verify_sqlite_db_integrity(db_file: Path) -> tuple[bool, str, dict[str, int]]:
    """Uji integritas file SQLite (.db) menggunakan PRAGMA integrity_check dan row count."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = Path(tmp.name)

    conn: sqlite3.Connection | None = None
    try:
        shutil.copy2(db_file, temp_db_path)
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # 1. PRAGMA integrity_check
        cursor.execute("PRAGMA integrity_check")
        integrity_res = cursor.fetchall()
        is_ok = len(integrity_res) == 1 and integrity_res[0][0] == "ok"
        if not is_ok:
            return False, f"Integritas gagal: {integrity_res}", {}

        # 2. PRAGMA foreign_key_check
        cursor.execute("PRAGMA foreign_key_check")
        fk_violations = cursor.fetchall()
        if fk_violations:
            return False, f"Pelanggaran Foreign Key: {fk_violations}", {}

        # 3. Hitung baris tabel
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cursor.fetchall()]

        table_counts: dict[str, int] = {}
        for tbl in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
                table_counts[tbl] = cursor.fetchone()[0]
            except Exception:
                table_counts[tbl] = -1

        return True, "PRAGMA integrity_check: ok", table_counts
    except Exception as exc:
        return False, f"Error membaca database: {exc}", {}
    finally:
        if conn is not None:
            conn.close()
        temp_db_path.unlink(missing_ok=True)


def find_latest_backup() -> Path | None:
    """Temukan file cadangan terbaru di direktori backups."""
    if not config.BACKUPS_DIR.is_dir():
        return None

    files = sorted(
        list(config.BACKUPS_DIR.glob("*.db"))
        + list(config.BACKUPS_DIR.glob("*.zip"))
        + list(config.BACKUPS_DIR.glob("*.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def run_restore_test(backup_path: Path | None = None) -> bool:
    """Jalankan pengujian pemulihan cadangan secara menyeluruh."""
    config.ensure_directories()
    target_path = backup_path

    if target_path is None:
        target_path = find_latest_backup()

    if target_path is None:
        print("[INFO] Tidak ada file backup. Membuat cadangan sementara...")
        target_path = perform_scheduled_backup()

    if target_path is None or not target_path.is_file():
        print(f"[ERROR] File cadangan tidak ditemukan: {target_path}", file=sys.stderr)
        return False

    print(f"[CHECK] Menguji pemulihan: {target_path.name} ({target_path.stat().st_size} bytes)")

    if target_path.suffix == ".db":
        ok, msg, counts = verify_sqlite_db_integrity(target_path)
        if not ok:
            print(f"[FAIL] Uji restore gagal: {msg}", file=sys.stderr)
            return False

        print(f"[OK] {msg}")
        print("Statistik Baris Data:")
        for tbl, count in sorted(counts.items()):
            print(f"  * {tbl}: {count} baris")
        return True

    # Format ZIP atau JSON
    try:
        from database.engine import session_scope
        from services.backup import _read_backup_dict_from_source, validate_backup_data

        raw, _, _ = _read_backup_dict_from_source(target_path)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            tmp_db_path = Path(tmp_db.name)

        test_engine = create_db_engine(f"sqlite:///{tmp_db_path.as_posix()}")
        try:
            from models import Base

            Base.metadata.create_all(test_engine)

            with session_scope(test_engine) as session:
                u_id = raw.get("user_id") or 1
                validate_backup_data(session, u_id, raw)

            print("[OK] Struktur data JSON/ZIP dan checksum valid.")
            return True
        finally:
            test_engine.dispose()
            tmp_db_path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[FAIL] Verifikasi backup JSON/ZIP gagal: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Uji pemulihan dan integritas file cadangan SQLite."
    )
    parser.add_argument(
        "--backup",
        type=str,
        default=None,
        help="Path ke file cadangan (.db, .zip, .json)",
    )
    args = parser.parse_args()

    b_path = Path(args.backup) if args.backup else None
    success = run_restore_test(b_path)
    if success:
        print("[SUCCESS] Uji pemulihan database berhasil 100%!")
        sys.exit(0)
    else:
        print("[ERROR] Uji pemulihan database gagal!", file=sys.stderr)
        sys.exit(1)
