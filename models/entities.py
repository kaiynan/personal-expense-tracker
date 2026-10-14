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
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
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

FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_MONTHLY = "monthly"
FREQUENCY_YEARLY = "yearly"

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_RESTORE = "restore"
ACTION_UNDO = "undo"

TRANSACTION_TYPES = (TYPE_INCOME, TYPE_EXPENSE, TYPE_TRANSFER, TYPE_ADJUSTMENT)
CATEGORY_TYPES = (TYPE_INCOME, TYPE_EXPENSE)
TRANSACTION_STATUSES = (STATUS_ACTIVE, STATUS_DELETED)
RECURRING_FREQUENCIES = (
    FREQUENCY_DAILY,
    FREQUENCY_WEEKLY,
    FREQUENCY_MONTHLY,
    FREQUENCY_YEARLY,
)
AUDIT_ACTIONS = (
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_DELETE,
    ACTION_RESTORE,
    ACTION_UNDO,
)

TARGET_CATEGORY = "category"
TARGET_PAYMENT_METHOD = "payment_method"
ALIAS_TARGET_TYPES = (TARGET_CATEGORY, TARGET_PAYMENT_METHOD)

REMINDER_DAILY = "daily"
REMINDER_WEEKLY = "weekly"
REMINDER_MONTHLY = "monthly"
REMINDER_TYPES = (REMINDER_DAILY, REMINDER_WEEKLY, REMINDER_MONTHLY)

NOTIFICATION_DAILY = "daily"
NOTIFICATION_WEEKLY = "weekly"
NOTIFICATION_MONTHLY = "monthly"
NOTIFICATION_RECURRING = "recurring"
NOTIFICATION_PREDICTIVE_BUDGET = "predictive_budget"
NOTIFICATION_PREDICTIVE_RUNWAY = "predictive_runway"
NOTIFICATION_TYPES = (
    NOTIFICATION_DAILY,
    NOTIFICATION_WEEKLY,
    NOTIFICATION_MONTHLY,
    NOTIFICATION_RECURRING,
    NOTIFICATION_PREDICTIVE_BUDGET,
    NOTIFICATION_PREDICTIVE_RUNWAY,
)

ROLE_USER = "user"
ROLE_ADMIN = "admin"
USER_ROLES = (ROLE_USER, ROLE_ADMIN)

USER_STATUS_PENDING = "pending"
USER_STATUS_ACTIVE = "active"
USER_STATUS_BLOCKED = "blocked"
USER_STATUSES = (USER_STATUS_PENDING, USER_STATUS_ACTIVE, USER_STATUS_BLOCKED)


def _utcnow() -> dt.datetime:
    """Waktu sekarang dalam UTC (tanpa tzinfo, sesuai cara DB menyimpan)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _in(column: str, values: tuple[str, ...]) -> str:
    """Buat ekspresi CHECK `kolom IN ('a', 'b', ...)`."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    """Pengguna aplikasi. V1 hanya satu pengguna, V3 mendukung multi-user dengan role & status."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", USER_ROLES), name="role_valid"),
        CheckConstraint(_in("status", USER_STATUSES), name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_USER, server_default=ROLE_USER)
    status: Mapped[str] = mapped_column(
        String(20), default=USER_STATUS_ACTIVE, server_default=USER_STATUS_ACTIVE
    )
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
        CheckConstraint("carryover_amount >= 0", name="carryover_non_negative"),
        CheckConstraint("length(month) = 7", name="month_format"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    month: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    amount: Mapped[int] = mapped_column(BigInteger)
    carryover_amount: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    is_rollover_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )


class WeeklyBudget(Base):
    """Budget mingguan (Senin s.d. Minggu). `category_id` NULL berarti budget total mingguan."""

    __tablename__ = "weekly_budgets"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "category_id",
            "week_start",
            name="uq_weekly_budgets_user_id_category_id_week_start",
        ),
        Index(
            "uq_weekly_budgets_total_per_week",
            "user_id",
            "week_start",
            unique=True,
            sqlite_where=text("category_id IS NULL"),
            postgresql_where=text("category_id IS NULL"),
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    week_start: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger)


class RecurringTransaction(Base):
    """Transaksi rutin (kos, internet, langganan, dsb.)."""

    __tablename__ = "recurring"
    __table_args__ = (
        CheckConstraint(_in("type", CATEGORY_TYPES), name="type_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(_in("frequency", RECURRING_FREQUENCIES), name="frequency_valid"),
        CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
            name="day_of_month_valid",
        ),
        Index("ix_recurring_user_active", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int] = mapped_column(BigInteger)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"))
    frequency: Mapped[str] = mapped_column(
        String(20), default=FREQUENCY_MONTHLY, server_default=FREQUENCY_MONTHLY
    )
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_generated_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class AuditLog(Base):
    """Log audit dan riwayat perubahan entitas transaksi (V2)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(_in("action", AUDIT_ACTIONS), name="action_valid"),
        CheckConstraint("length(trim(action)) > 0", name="action_not_blank"),
        CheckConstraint("length(trim(entity_type)) > 0", name="entity_type_not_blank"),
        Index("ix_audit_log_user_created", "user_id", "created_at"),
        Index("ix_audit_log_user_entity", "user_id", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    old_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_undone: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class QuickAlias(Base):
    """Tabel alias input cepat untuk kategori dan metode pembayaran (V2)."""

    __tablename__ = "quick_aliases"
    __table_args__ = (
        UniqueConstraint("user_id", "alias", name="uq_quick_aliases_user_id_alias"),
        CheckConstraint("length(trim(alias)) > 0", name="alias_not_blank"),
        CheckConstraint(_in("target_type", ALIAS_TARGET_TYPES), name="target_type_valid"),
        CheckConstraint(
            "(target_type = 'category' AND category_id IS NOT NULL AND method_id IS NULL) "
            "OR (target_type = 'payment_method' AND method_id IS NOT NULL "
            "AND category_id IS NULL)",
            name="alias_target_match",
        ),
        Index("ix_quick_aliases_user_alias", "user_id", "alias"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    alias: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(20))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ReminderSetting(Base):
    """Pengaturan pengingat harian dan ringkasan otomatis pengguna (V2)."""

    __tablename__ = "reminder_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_reminder_settings_user_id"),
        CheckConstraint("length(trim(daily_time)) = 5", name="ck_reminder_settings_time_format"),
        Index("ix_reminder_settings_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_daily_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    daily_time: Mapped[str] = mapped_column(String(5), default="20:00", server_default="20:00")
    is_weekly_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    is_monthly_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_daily_sent: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_weekly_sent: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_monthly_sent: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Invite(Base):
    """Kode undangan registrasi pengguna baru (V3)."""

    __tablename__ = "invites"
    __table_args__ = (
        UniqueConstraint("code", name="uq_invites_code"),
        CheckConstraint("length(trim(code)) > 0", name="code_not_blank"),
        CheckConstraint(
            "(is_used = true AND used_by_user_id IS NOT NULL) "
            "OR (is_used = false AND used_by_user_id IS NULL)",
            name="used_consistency",
        ),
        Index("ix_invites_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    code: Mapped[str] = mapped_column(String(50))
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    used_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class UserSetting(Base):
    """Pengaturan zona waktu dan jadwal notifikasi per pengguna (V3)."""

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_settings_user_id"),
        CheckConstraint("length(trim(timezone)) > 0", name="ck_user_settings_tz_not_blank"),
        CheckConstraint("length(trim(daily_time)) = 5", name="ck_user_settings_time_format"),
        Index("ix_user_settings_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    timezone: Mapped[str] = mapped_column(
        String(50), default=config.TIMEZONE_NAME, server_default=config.TIMEZONE_NAME
    )
    is_daily_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    daily_time: Mapped[str] = mapped_column(String(5), default="20:00", server_default="20:00")
    is_recurring_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    is_weekly_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    is_monthly_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SentNotification(Base):
    """Catatan idempotensi pengiriman notifikasi terjadwal per periode pengguna (V3)."""

    __tablename__ = "sent_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_type",
            "period_key",
            name="uq_sent_notifications_user_type_period",
        ),
        CheckConstraint(
            _in("notification_type", NOTIFICATION_TYPES),
            name="ck_sent_notifications_type_valid",
        ),
        CheckConstraint(
            "length(trim(period_key)) > 0",
            name="ck_sent_notifications_period_not_blank",
        ),
        Index("ix_sent_notifications_user_type", "user_id", "notification_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    notification_type: Mapped[str] = mapped_column(String(30))
    period_key: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class WebLoginOtp(Base):
    """Kode OTP untuk autentikasi login Web Dashboard (Fase W)."""

    __tablename__ = "web_login_otps"
    __table_args__ = (
        CheckConstraint("length(trim(code_hash)) > 0", name="code_hash_not_blank"),
        Index("ix_web_login_otps_user", "user_id"),
        Index("ix_web_login_otps_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
