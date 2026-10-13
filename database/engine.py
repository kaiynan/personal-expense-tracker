"""Engine dan session SQLAlchemy.

Tidak ada engine/session global: pemanggil membuatnya lewat fungsi di sini
lalu meneruskannya secara eksplisit.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import config

logger = logging.getLogger(__name__)

# Waktu tunggu (ms) bila database sedang dikunci proses lain (CLI vs bot).
SQLITE_BUSY_TIMEOUT_MS = 5000


def create_db_engine(url: str | None = None) -> Engine:
    """Buat engine. Untuk SQLite, aktifkan PRAGMA yang dibutuhkan aplikasi.

    Args:
        url: URL database; default `config.DATABASE_URL`.
    """
    url = url or config.DATABASE_URL
    if url.startswith("sqlite:///"):
        raw_path = url[len("sqlite:///") :]
        if raw_path and not raw_path.startswith(":memory:"):
            from pathlib import Path

            db_dir = Path(raw_path).parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                logger.warning("Gagal membuat direktori database %s: %s", db_dir, exc)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)
        _restrict_file_permissions(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    """Pasang event koneksi: foreign key, WAL, dan busy_timeout (khusus SQLite)."""
    in_memory = engine.url.database in (None, "", ":memory:")

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        if not in_memory:  # WAL tidak berlaku untuk database di memori
            cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()


def _restrict_file_permissions(engine: Engine) -> None:
    """Di Linux/macOS, batasi file database agar hanya bisa dibaca pemiliknya (0600).

    File berisi data keuangan pribadi. Di Windows langkah ini dilewati.
    """
    database = engine.url.database
    if os.name != "posix" or database in (None, "", ":memory:"):
        return
    try:
        with engine.connect():  # memastikan file sudah dibuat
            pass
        if os.path.isfile(database):
            os.chmod(database, 0o600)
    except Exception as exc:
        logger.warning("Tidak bisa mengatur izin file database: %s (%s)", database, exc)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Buat pabrik session untuk engine tertentu."""
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Satu unit kerja: commit jika sukses, rollback jika ada error.

    Dipakai per operasi service, sehingga transaksi database bersifat atomik.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
