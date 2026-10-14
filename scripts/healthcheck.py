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
    """Jalankan healthcheck: pastikan database sehat dan endpoint /health (jika aktif)."""
    # 1. Pastikan integritas dan konektivitas basis data berfungsi
    if not check_database_health():
        return False

    port_str = os.environ.get("PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    # 2. Jika HTTP port terbuka, pastikan statusnya healthy
    # Jika koneksi ditolak (mode bot murni), konektivitas database di atas sudah mencukupi
    url = f"http://127.0.0.1:{port}/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DockerHealthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "healthy"
    except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, OSError):
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if run_healthcheck():
        sys.exit(0)
    sys.exit(1)
