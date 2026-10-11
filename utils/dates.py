"""Utilitas tanggal: format tampilan DD-MM-YYYY dan "hari ini" per zona waktu."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import config


def today(timezone_name: str = config.TIMEZONE_NAME) -> date:
    """Tanggal hari ini menurut zona waktu pengguna."""
    return datetime.now(ZoneInfo(timezone_name)).date()


def format_display(value: date) -> str:
    """Ubah tanggal menjadi teks tampilan `DD-MM-YYYY`."""
    return value.strftime(config.DATE_FORMAT_DISPLAY)
