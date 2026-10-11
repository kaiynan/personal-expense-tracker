"""Format tampilan angka rupiah."""


def format_rupiah(amount: int) -> str:
    """Contoh: 1500000 -> 'Rp1.500.000', -50000 -> '-Rp50.000'."""
    sign = "-" if amount < 0 else ""
    return f"{sign}Rp{abs(amount):,}".replace(",", ".")
