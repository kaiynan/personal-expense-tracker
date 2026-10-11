"""Model ORM seluruh tabel V1.

Aturan domain dijaga langsung di skema database memakai SQL portabel
(`CHECK`, `UNIQUE`, `FOREIGN KEY`), tanpa fitur khusus SQLite:

- nominal disimpan sebagai integer rupiah (`BigInteger`), tidak pernah float;
- tanggal transaksi bertipe `Date` (ISO `YYYY-MM-DD`);
- `created_at`/`updated_at` disimpan sebagai UTC;
- setiap tabel punya `user_id`.

Catatan: `import datetime as dt` dipakai karena kolom bernama `date` akan
menimpa nama `date` di dalam body class.
"""

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

import config
from models.base import Base

# --- Konstanta nilai yang diizinkan --------------------------------------------
TYPE_INCOME = "income"
TYPE_EXPENSE = "expense"
TYPE_TRANSFER = "transfer"
TYPE_ADJUSTMENT = "adjustment"

STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"

TRANSACTION_TYPES = (TYPE_INCOME, TYPE_EXPENSE, TYPE_TRANSFER, TYPE_ADJUSTMENT)
CATEGORY_TYPES = (TYPE_INCOME, TYPE_EXPENSE)
TRANSACTION_STATUSES = (STATUS_ACTIVE, STATUS_DELETED)


def _utcnow() -> dt.datetime:
    """Waktu sekarang dalam UTC (tanpa tzinfo, sesuai cara DB menyimpan)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _in(column: str, values: tuple[str, ...]) -> str:
    """Buat ekspresi CHECK `kolom IN ('a', 'b', ...)`."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    """Pengguna aplikasi. V1 hanya satu pengguna, tetapi skema sudah multi-user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(
        String(50), default=config.TIMEZONE_NAME, server_default=config.TIMEZONE_NAME
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class PaymentMethod(Base):
    """Metode pembayaran (Cash, Bank, E-Wallet, ...)."""

    __tablename__ = "payment_methods"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_payment_methods_user_id_name"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(50))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Category(Base):
    """Kategori pemasukan/pengeluaran. Tidak dihapus jika sudah dipakai, tetapi diarsipkan."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "type", "name", name="uq_categories_user_id_type_name"),
        CheckConstraint(_in("type", CATEGORY_TYPES), name="type_valid"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(50))
    type: Mapped[str] = mapped_column(String(10))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Transaction(Base):
    """Transaksi: income, expense, transfer, atau adjustment (koreksi saldo)."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(_in("type", TRANSACTION_TYPES), name="type_valid"),
        CheckConstraint(_in("status", TRANSACTION_STATUSES), name="status_valid"),
        # adjustment boleh negatif tetapi tidak nol; jenis lain harus > 0.
        CheckConstraint(
            "(type = 'adjustment' AND amount <> 0) OR (type <> 'adjustment' AND amount > 0)",
            name="amount_sign",
        ),
        # transfer wajib punya tujuan yang berbeda dari asal; jenis lain tanpa tujuan.
        CheckConstraint(
            "(type = 'transfer' AND to_method_id IS NOT NULL AND to_method_id <> method_id) "
            "OR (type <> 'transfer' AND to_method_id IS NULL)",
            name="transfer_target",
        ),
        Index("ix_transactions_user_status_date", "user_id", "status", "date"),
        Index("ix_transactions_user_method", "user_id", "method_id"),
        Index("ix_transactions_user_to_method", "user_id", "to_method_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int] = mapped_column(BigInteger)
    date: Mapped[dt.date] = mapped_column(Date)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"))
    to_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    related_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(10), default=STATUS_ACTIVE, server_default=STATUS_ACTIVE
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class OpeningBalance(Base):
    """Saldo awal per metode pembayaran beserta tanggal berlakunya."""

    __tablename__ = "opening_balances"
    __table_args__ = (
        UniqueConstraint("user_id", "method_id", name="uq_opening_balances_user_id_method_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    effective_date: Mapped[dt.date] = mapped_column(Date)


class Budget(Base):
    """Budget bulanan. `category_id` NULL berarti budget total bulanan."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "category_id", "month", name="uq_budgets_user_id_category_id_month"
        ),
        # NULL dianggap berbeda oleh UNIQUE, jadi budget total butuh index parsial
        # agar tidak bisa ada dua budget total untuk bulan yang sama.
        Index(
            "uq_budgets_total_per_month",
            "user_id",
            "month",
            unique=True,
            sqlite_where=text("category_id IS NULL"),
            postgresql_where=text("category_id IS NULL"),
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(month) = 7", name="month_format"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    month: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    amount: Mapped[int] = mapped_column(BigInteger)
