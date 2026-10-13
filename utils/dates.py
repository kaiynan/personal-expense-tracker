"""Utilitas tanggal & zona waktu: WIB/WITA/WIT, 'hari ini', dan batas periode per zona waktu."""

import calendar
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config
from utils.errors import ValidationError

TIMEZONE_ALIASES: dict[str, str] = {
    "WIB": "Asia/Jakarta",
    "WITA": "Asia/Makassar",
    "WIT": "Asia/Jayapura",
    "ASIA/JAKARTA": "Asia/Jakarta",
    "ASIA/PONTIANAK": "Asia/Pontianak",
    "ASIA/MAKASSAR": "Asia/Makassar",
    "ASIA/UJUNG_PANDANG": "Asia/Makassar",
    "ASIA/JAYAPURA": "Asia/Jayapura",
    "UTC": "UTC",
}

TIMEZONE_LABELS: dict[str, str] = {
    "Asia/Jakarta": "WIB (Asia/Jakarta)",
    "Asia/Pontianak": "WIB (Asia/Pontianak)",
    "Asia/Makassar": "WITA (Asia/Makassar)",
    "Asia/Jayapura": "WIT (Asia/Jayapura)",
}


def normalize_timezone(timezone_name: str) -> str:
    """Validasi dan normalisasi nama zona waktu (mendukung kode WIB, WITA, WIT, atau IANA)."""
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValidationError(
            "Zona waktu tidak boleh kosong. Gunakan WIB, WITA, WIT, "
            "atau nama IANA (mis. Asia/Jakarta)."
        )
    cleaned = timezone_name.strip()
    upper = cleaned.upper()
    resolved = TIMEZONE_ALIASES.get(upper, cleaned)
    try:
        ZoneInfo(resolved)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValidationError(
            f"Zona waktu tidak dikenali: '{timezone_name}'. "
            "Pilih WIB (Asia/Jakarta), WITA (Asia/Makassar), atau WIT (Asia/Jayapura)."
        ) from exc
    return resolved


def format_timezone_label(timezone_name: str) -> str:
    """Kembalikan label tampilan zona waktu (mis. 'WIB (Asia/Jakarta)')."""
    try:
        norm = normalize_timezone(timezone_name)
    except ValidationError:
        return timezone_name
    return TIMEZONE_LABELS.get(norm, norm)


def _utcnow() -> datetime:
    """Ambil waktu sekarang dalam UTC (dapat di-monkeypatch saat pengujian)."""
    return datetime.now(UTC)


def now_in_tz(
    timezone_name: str = config.TIMEZONE_NAME,
    utc_now: datetime | None = None,
) -> datetime:
    """Waktu sekarang menurut zona waktu pengguna."""
    norm_tz = normalize_timezone(timezone_name)
    tz = ZoneInfo(norm_tz)
    base_utc = utc_now if utc_now is not None else _utcnow()
    base_utc = base_utc.replace(tzinfo=UTC) if base_utc.tzinfo is None else base_utc.astimezone(UTC)
    return base_utc.astimezone(tz)


def today(
    timezone_name: str = config.TIMEZONE_NAME,
    utc_now: datetime | None = None,
) -> date:
    """Tanggal hari ini menurut zona waktu pengguna."""
    return now_in_tz(timezone_name, utc_now=utc_now).date()


def week_bounds(
    timezone_name: str = config.TIMEZONE_NAME,
    on: date | None = None,
    utc_now: datetime | None = None,
) -> tuple[date, date]:
    """Batas awal (Senin) dan akhir (Minggu) minggu menurut zona waktu pengguna."""
    ref = on if on is not None else today(timezone_name, utc_now=utc_now)
    start = ref - timedelta(days=ref.weekday())
    end = start + timedelta(days=6)
    return start, end


def month_bounds(
    timezone_name: str = config.TIMEZONE_NAME,
    on: date | None = None,
    utc_now: datetime | None = None,
) -> tuple[date, date]:
    """Batas awal (tanggal 1) dan akhir bulan menurut zona waktu pengguna."""
    ref = on if on is not None else today(timezone_name, utc_now=utc_now)
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    return date(ref.year, ref.month, 1), date(ref.year, ref.month, last_day)


def format_display(value: date) -> str:
    """Ubah tanggal menjadi teks tampilan `DD-MM-YYYY`."""
    return value.strftime(config.DATE_FORMAT_DISPLAY)
