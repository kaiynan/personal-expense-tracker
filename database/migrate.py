"""Menjalankan migrasi Alembic dari kode Python."""

from alembic.config import Config

import config
from alembic import command


def make_alembic_config(url: str | None = None) -> Config:
    """Buat konfigurasi Alembic yang menunjuk ke folder `alembic/` proyek."""
    cfg = Config(str(config.BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BASE_DIR / "alembic"))
    # '%' harus di-escape karena configparser memakainya untuk interpolasi.
    cfg.set_main_option("sqlalchemy.url", (url or config.DATABASE_URL).replace("%", "%%"))
    return cfg


def upgrade_to_head(url: str | None = None) -> None:
    """Terapkan semua migrasi sampai versi terbaru."""
    command.upgrade(make_alembic_config(url), "head")
