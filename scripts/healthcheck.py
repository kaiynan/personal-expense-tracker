"""Healthcheck container Docker untuk memeriksa koneksi dan integritas database."""

from __future__ import annotations

import sys

from sqlalchemy import text

import config
from database.engine import create_db_engine


def run_healthcheck() -> bool:
    """Periksa konektivitas database SQLite dan kesiapan sistem."""
    try:
        config.ensure_directories()
        engine = create_db_engine(config.DATABASE_URL)
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1")).scalar()
                if result != 1:
                    return False
            return True
        finally:
            engine.dispose()
    except Exception as exc:
        print(f"Healthcheck error: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    if run_healthcheck():
        sys.exit(0)
    sys.exit(1)
