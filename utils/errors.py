"""Exception aplikasi. Pesannya berbahasa Indonesia dan aman ditampilkan ke pengguna."""


class AppError(Exception):
    """Induk semua error yang sengaja dilempar aplikasi."""

    @property
    def message(self) -> str:
        return str(self)


class ValidationError(AppError, ValueError):
    """Input tidak valid (nominal, tanggal, keterangan, kategori, metode, ...)."""


class NotFoundError(AppError, LookupError):
    """Data tidak ditemukan atau bukan milik pengguna yang meminta."""
