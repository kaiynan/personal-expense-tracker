"""Struktur hasil service: data yang dibuat/diubah beserta daftar peringatan.

Peringatan tidak memblokir operasi; data tetap tersimpan. Antarmuka (CLI/bot)
cukup menampilkan `message`, sedangkan `code` dipakai untuk logika/tes.
"""

from dataclasses import dataclass, field

from models import AuditLog, OpeningBalance, Transaction

# Kode peringatan
NEGATIVE_BALANCE = "negative_balance"
BEFORE_OPENING_DATE = "before_opening_date"
NEWER_ADJUSTMENT = "newer_adjustment"


@dataclass(frozen=True)
class ServiceWarning:
    """Satu peringatan untuk pengguna."""

    code: str
    message: str
    method_id: int | None = None


@dataclass
class TransactionResult:
    """Hasil tambah/edit/hapus/pulihkan transaksi."""

    transaction: Transaction
    warnings: list[ServiceWarning] = field(default_factory=list)


@dataclass
class TransferResult:
    """Hasil transfer. `admin_fee` berisi transaksi pengeluaran biaya admin (jika ada)."""

    transfer: Transaction
    admin_fee: Transaction | None = None
    warnings: list[ServiceWarning] = field(default_factory=list)


@dataclass
class OpeningBalanceResult:
    """Hasil mengatur saldo awal."""

    opening_balance: OpeningBalance
    warnings: list[ServiceWarning] = field(default_factory=list)


@dataclass
class AdjustmentResult:
    """Hasil koreksi saldo.

    `transaction` bernilai `None` bila saldo tercatat sudah sama dengan saldo
    sebenarnya (selisih 0), karena adjustment bernominal 0 tidak diizinkan.
    """

    transaction: Transaction | None
    previous_balance: int
    actual_balance: int
    difference: int
    warnings: list[ServiceWarning] = field(default_factory=list)


@dataclass
class UndoResult:
    """Hasil pembatalan aksi (undo)."""

    audit_log: AuditLog
    transaction: Transaction | None = None
    action_undone: str = ""
    warnings: list[ServiceWarning] = field(default_factory=list)


def warning_codes(warnings: list[ServiceWarning]) -> list[str]:
    """Daftar kode peringatan (praktis untuk tes dan logging)."""
    return [w.code for w in warnings]
