"""Healthcheck container Docker untuk memeriksa endpoint /health dan integritas database."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from sqlalchemy import text

import config
from database.engine import create_db_engine


def check_http_health(port: int) -> bool:
    """Periksa endpoint HTTP /health pada web dashboard."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DockerHealthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                body = resp.read()
                data = json.loads(body.decode("utf-8"))
                return data.get("status") == "healthy"
            return False
    except urllib.error.URLError:
        return False
    except Exception:
        return False


def check_database_health() -> bool:
    """Fallback: periksa konektivitas database SQLite jika web server belum/tidak aktif."""
    try:
        config.ensure_directories()
        engine = create_db_engine(config.DATABASE_URL)
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1")).scalar()
                return result == 1
        finally:
            engine.dispose()
    except Exception as exc:
        print(f"Healthcheck database error: {exc}", file=sys.stderr)
        return False


def run_healthcheck() -> bool:
    """Jalankan healthcheck: utamakan HTTP /health, fallback ke database."""
    port_str = os.environ.get("PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    # Coba HTTP /health terlebih dahulu
    if check_http_health(port):
        return True

    # Fallback jika container dijalankan mode bot murni (--bot)
    return check_database_health()


if __name__ == "__main__":
    if run_healthcheck():
        sys.exit(0)
    sys.exit(1)
