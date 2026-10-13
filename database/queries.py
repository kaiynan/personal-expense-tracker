"""Query baca data pengguna (selain perhitungan saldo, lihat `balances.py`).

Semua query ditulis dengan SQLAlchemy ORM (otomatis berparameter, portabel) dan
**selalu** memfilter `user_id`. Data milik pengguna lain diperlakukan sama
seperti data yang tidak ada (mengembalikan `None`), sehingga keberadaannya
tidak bocor.
"""

from datetime import date
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

import config
from models import (
    ROLE_ADMIN,
    ROLE_USER,
    STATUS_ACTIVE,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    USER_STATUS_ACTIVE,
    USER_STATUS_PENDING,
    AuditLog,
    Budget,
    Category,
    Invite,
    OpeningBalance,
    PaymentMethod,
    QuickAlias,
    RecurringTransaction,
    ReminderSetting,
    SentNotification,
    Transaction,
    User,
    UserSetting,
    WeeklyBudget,
)


def get_user(session: Session, user_id: int) -> User | None:
    """Ambil pengguna berdasarkan id."""
    return session.scalars(select(User).where(User.id == user_id)).first()


def get_user_by_telegram_id(session: Session, telegram_id: int) -> User | None:
    """Ambil pengguna berdasarkan telegram_id."""
    return session.scalars(select(User).where(User.telegram_id == telegram_id)).first()


def ensure_telegram_user(session: Session, telegram_id: int, name: str = "User") -> User:
    """Petakan ID Telegram ke baris pengguna (bootstrap admin atau buat baru)."""
    user = get_user_by_telegram_id(session, telegram_id)
    if user is not None:
        if telegram_id in config.ALLOWED_TELEGRAM_IDS and user.role != ROLE_ADMIN:
            user.role = ROLE_ADMIN
            user.status = USER_STATUS_ACTIVE
            session.flush()
        return user

    is_bootstrap_admin = telegram_id in config.ALLOWED_TELEGRAM_IDS
    role = ROLE_ADMIN if is_bootstrap_admin else ROLE_USER

    # Petakan ke pengguna default V1 yang belum punya telegram_id hanya untuk admin pertama
    # (atau jika belum ada pengguna ber-telegram_id sama sekali).
    mapped_count = (
        session.scalar(select(func.count(User.id)).where(User.telegram_id.is_not(None))) or 0
    )
    if mapped_count == 0 and (is_bootstrap_admin or not config.ALLOWED_TELEGRAM_IDS):
        unmapped_user = session.scalars(
            select(User).where(User.telegram_id.is_(None)).order_by(User.id)
        ).first()
        if unmapped_user is not None:
            unmapped_user.telegram_id = telegram_id
            if is_bootstrap_admin:
                unmapped_user.role = ROLE_ADMIN
                unmapped_user.status = USER_STATUS_ACTIVE
            session.flush()
            return unmapped_user

    user = User(
        telegram_id=telegram_id,
        name=name.strip() or "User",
        role=role,
        status=USER_STATUS_ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def register_telegram_user(
    session: Session, telegram_id: int, name: str = "User"
) -> tuple[User, bool]:
    """Daftarkan pengguna dari `/start` Telegram (V3).

    - Jika `telegram_id` ada di `config.ALLOWED_TELEGRAM_IDS`, otomatis menjadi
      `role='admin'` dan `status='active'` (serta memetakan user V1 bila belum dipetakan).
    - Jika belum terdaftar dan tidak di `ALLOWED_TELEGRAM_IDS`, dibuat dengan
      `role='user'` dan `status='pending'`.

    Returns:
        Tuple `(user, is_new)`.
    """
    existing = get_user_by_telegram_id(session, telegram_id)
    if existing is not None:
        if telegram_id in config.ALLOWED_TELEGRAM_IDS and existing.role != ROLE_ADMIN:
            existing.role = ROLE_ADMIN
            existing.status = USER_STATUS_ACTIVE
            session.flush()
        return existing, False

    if telegram_id in config.ALLOWED_TELEGRAM_IDS:
        user = ensure_telegram_user(session, telegram_id, name=name)
        return user, True

    user = User(
        telegram_id=telegram_id,
        name=name.strip() or "User",
        role=ROLE_USER,
        status=USER_STATUS_PENDING,
    )
    session.add(user)
    session.flush()
    return user, True


def list_users(session: Session, *, status: str | None = None) -> list[User]:
    """Daftar seluruh pengguna aplikasi, terurut berdasarkan ID."""
    stmt = select(User)
    if status is not None:
        stmt = stmt.where(User.status == status)
    return list(session.scalars(stmt.order_by(User.id.asc())))


def get_method(session: Session, user_id: int, method_id: int) -> PaymentMethod | None:
    """Ambil metode pembayaran milik `user_id`."""
    return session.scalars(
        select(PaymentMethod).where(PaymentMethod.user_id == user_id, PaymentMethod.id == method_id)
    ).first()


def list_methods(session: Session, user_id: int) -> list[PaymentMethod]:
    """Semua metode pembayaran milik `user_id` (termasuk yang diarsipkan)."""
    return list(
        session.scalars(
            select(PaymentMethod).where(PaymentMethod.user_id == user_id).order_by(PaymentMethod.id)
        )
    )


def get_category(session: Session, user_id: int, category_id: int) -> Category | None:
    """Ambil kategori milik `user_id`."""
    return session.scalars(
        select(Category).where(Category.user_id == user_id, Category.id == category_id)
    ).first()


def get_system_category(
    session: Session, user_id: int, name: str, category_type: str
) -> Category | None:
    """Ambil kategori sistem (mis. "Biaya Admin") milik `user_id`."""
    return session.scalars(
        select(Category).where(
            Category.user_id == user_id,
            Category.name == name,
            Category.type == category_type,
            Category.is_system.is_(True),
        )
    ).first()


def get_category_by_name(
    session: Session, user_id: int, name: str, category_type: str
) -> Category | None:
    """Ambil kategori milik `user_id` berdasarkan nama (case-insensitive) dan jenis."""
    return session.scalars(
        select(Category).where(
            Category.user_id == user_id,
            func.lower(Category.name) == func.lower(name),
            Category.type == category_type,
        )
    ).first()


def list_categories(
    session: Session,
    user_id: int,
    *,
    type_: str | None = None,
    include_archived: bool = False,
    include_system: bool = False,
) -> list[Category]:
    """Daftar kategori milik `user_id`."""
    stmt = select(Category).where(Category.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(Category.is_archived.is_(False))
    if not include_system:
        stmt = stmt.where(Category.is_system.is_(False))
    if type_ is not None:
        stmt = stmt.where(Category.type == type_)
    return list(session.scalars(stmt.order_by(Category.id)))


def get_opening_balance(session: Session, user_id: int, method_id: int) -> OpeningBalance | None:
    """Ambil saldo awal satu metode milik `user_id`."""
    return session.scalars(
        select(OpeningBalance).where(
            OpeningBalance.user_id == user_id, OpeningBalance.method_id == method_id
        )
    ).first()


def get_transaction(
    session: Session, user_id: int, transaction_id: int, include_deleted: bool = False
) -> Transaction | None:
    """Ambil satu transaksi milik `user_id`. Default hanya yang berstatus `active`."""
    stmt = select(Transaction).where(
        Transaction.user_id == user_id, Transaction.id == transaction_id
    )
    if not include_deleted:
        stmt = stmt.where(Transaction.status == STATUS_ACTIVE)
    return session.scalars(stmt).first()


def list_transactions(
    session: Session,
    user_id: int,
    *,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    limit: int | None = None,
) -> list[Transaction]:
    """Daftar transaksi milik `user_id`, terbaru lebih dulu.

    Args:
        type_: filter jenis transaksi.
        category_id: filter kategori.
        method_id: filter metode; cocok sebagai metode asal maupun tujuan transfer.
        start, end: rentang tanggal (inklusif).
        keyword: cari (tanpa membedakan huruf besar/kecil) di keterangan atau
            nama kategori. Karakter `%` dan `_` diperlakukan sebagai teks biasa.
        include_deleted: ikut sertakan transaksi yang sudah dihapus (soft delete).
        limit: batas jumlah baris.
    """
    t, c = Transaction, Category
    stmt = (
        select(t)
        # Join kategori juga dibatasi user_id agar tidak bisa mencocokkan
        # kategori milik pengguna lain.
        .outerjoin(c, and_(c.id == t.category_id, c.user_id == t.user_id))
        .where(t.user_id == user_id)
    )
    if not include_deleted:
        stmt = stmt.where(t.status == STATUS_ACTIVE)
    if type_ is not None:
        stmt = stmt.where(t.type == type_)
    if category_id is not None:
        stmt = stmt.where(t.category_id == category_id)
    if method_id is not None:
        stmt = stmt.where(or_(t.method_id == method_id, t.to_method_id == method_id))
    if start is not None:
        stmt = stmt.where(t.date >= start)
    if end is not None:
        stmt = stmt.where(t.date <= end)
    if keyword:
        stmt = stmt.where(
            or_(
                t.note.icontains(keyword, autoescape=True),
                c.name.icontains(keyword, autoescape=True),
            )
        )
    stmt = stmt.order_by(t.date.desc(), t.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def count_transactions_for_user_on_date(session: Session, user_id: int, tx_date: date) -> int:
    """Hitung jumlah transaksi aktif milik user_id pada tanggal tertentu."""
    stmt = select(func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.date == tx_date,
        Transaction.status == STATUS_ACTIVE,
    )
    return session.scalar(stmt) or 0


def get_related_transactions(
    session: Session, user_id: int, parent_transaction_id: int, include_deleted: bool = False
) -> list[Transaction]:
    """Ambil transaksi yang terhubung ke `parent_transaction_id` milik `user_id`."""
    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.related_transaction_id == parent_transaction_id,
    )
    if not include_deleted:
        stmt = stmt.where(Transaction.status == STATUS_ACTIVE)
    return list(session.scalars(stmt))


def get_budget(
    session: Session, user_id: int, month: str, category_id: int | None = None
) -> Budget | None:
    """Ambil budget milik `user_id` untuk bulan tertentu (`YYYY-MM`)."""
    stmt = select(Budget).where(Budget.user_id == user_id, Budget.month == month)
    if category_id is None:
        stmt = stmt.where(Budget.category_id.is_(None))
    else:
        stmt = stmt.where(Budget.category_id == category_id)
    return session.scalars(stmt).first()


def set_budget(
    session: Session,
    user_id: int,
    month: str,
    amount: int,
    category_id: int | None = None,
    *,
    carryover_amount: int | None = None,
    is_rollover_enabled: bool | None = None,
) -> Budget:
    """Buat atau perbarui budget milik `user_id`."""
    budget = get_budget(session, user_id, month, category_id)
    if budget is None:
        budget = Budget(
            user_id=user_id,
            month=month,
            amount=amount,
            category_id=category_id,
            carryover_amount=carryover_amount if carryover_amount is not None else 0,
            is_rollover_enabled=(is_rollover_enabled if is_rollover_enabled is not None else False),
        )
        session.add(budget)
    else:
        budget.amount = amount
        if carryover_amount is not None:
            budget.carryover_amount = carryover_amount
        if is_rollover_enabled is not None:
            budget.is_rollover_enabled = is_rollover_enabled
    return budget


def get_weekly_budget(
    session: Session,
    user_id: int,
    week_start: date,
    category_id: int | None = None,
) -> WeeklyBudget | None:
    """Ambil budget mingguan milik `user_id` untuk minggu tertentu (`week_start` Senin)."""
    stmt = select(WeeklyBudget).where(
        WeeklyBudget.user_id == user_id, WeeklyBudget.week_start == week_start
    )
    if category_id is None:
        stmt = stmt.where(WeeklyBudget.category_id.is_(None))
    else:
        stmt = stmt.where(WeeklyBudget.category_id == category_id)
    return session.scalars(stmt).first()


def set_weekly_budget(
    session: Session,
    user_id: int,
    week_start: date,
    amount: int,
    category_id: int | None = None,
) -> WeeklyBudget:
    """Buat atau perbarui budget mingguan milik `user_id`."""
    wb = get_weekly_budget(session, user_id, week_start, category_id)
    if wb is None:
        wb = WeeklyBudget(
            user_id=user_id,
            week_start=week_start,
            amount=amount,
            category_id=category_id,
        )
        session.add(wb)
    else:
        wb.amount = amount
    return wb


def list_weekly_budgets(session: Session, user_id: int, week_start: date) -> list[WeeklyBudget]:
    """Ambil semua budget mingguan milik `user_id` untuk minggu tertentu."""
    stmt = (
        select(WeeklyBudget)
        .where(WeeklyBudget.user_id == user_id, WeeklyBudget.week_start == week_start)
        .order_by(WeeklyBudget.category_id.is_not(None), WeeklyBudget.id)
    )
    return list(session.scalars(stmt))


def get_monthly_summary(
    session: Session, user_id: int, start_date: date, end_date: date
) -> tuple[int, int, int]:
    """Hitung total pemasukan, pengeluaran, dan jumlah transaksi aktif dalam rentang tanggal.

    Returns:
        (total_pemasukan, total_pengeluaran, jumlah_transaksi)
    """
    t = Transaction
    stmt = select(
        func.coalesce(func.sum(case((t.type == TYPE_INCOME, t.amount), else_=0)), 0),
        func.coalesce(func.sum(case((t.type == TYPE_EXPENSE, t.amount), else_=0)), 0),
        func.count(t.id),
    ).where(
        t.user_id == user_id,
        t.status == STATUS_ACTIVE,
        t.date >= start_date,
        t.date <= end_date,
    )
    income, expense, count = session.execute(stmt).one()
    return int(income), int(expense), int(count)


def list_budgets(session: Session, user_id: int, month: str) -> list[Budget]:
    """Ambil semua budget milik `user_id` untuk bulan tertentu (total dan per kategori)."""
    stmt = (
        select(Budget)
        .where(Budget.user_id == user_id, Budget.month == month)
        .order_by(Budget.category_id.is_not(None), Budget.id)
    )
    return list(session.scalars(stmt))


def get_monthly_category_expenses(
    session: Session, user_id: int, start_date: date, end_date: date
) -> dict[int, int]:
    """Total pengeluaran per kategori dalam rentang tanggal untuk transaksi aktif."""
    t = Transaction
    stmt = (
        select(
            t.category_id,
            func.coalesce(func.sum(t.amount), 0),
        )
        .where(
            t.user_id == user_id,
            t.status == STATUS_ACTIVE,
            t.type == TYPE_EXPENSE,
            t.category_id.is_not(None),
            t.date >= start_date,
            t.date <= end_date,
        )
        .group_by(t.category_id)
    )
    return {int(cid): int(amt) for cid, amt in session.execute(stmt)}


def get_newer_adjustments(
    session: Session,
    user_id: int,
    method_id: int,
    after_date: date,
    exclude_id: int | None = None,
) -> list[Transaction]:
    """Cari transaksi koreksi saldo (adjustment) aktif pada metode yang sama.

    Hanya mencakup transaksi bertanggal >= after_date.
    """
    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.status == STATUS_ACTIVE,
        Transaction.type == TYPE_ADJUSTMENT,
        Transaction.method_id == method_id,
        Transaction.date >= after_date,
    )
    if exclude_id is not None:
        stmt = stmt.where(Transaction.id != exclude_id)
    return list(session.scalars(stmt.order_by(Transaction.date.desc(), Transaction.id.desc())))


def get_recurring(session: Session, user_id: int, recurring_id: int) -> RecurringTransaction | None:
    """Ambil satu transaksi rutin milik user_id."""
    return session.scalars(
        select(RecurringTransaction).where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.id == recurring_id,
        )
    ).first()


def list_recurring(
    session: Session,
    user_id: int,
    *,
    is_active: bool | None = None,
) -> list[RecurringTransaction]:
    """Daftar transaksi rutin milik user_id."""
    stmt = select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
    if is_active is not None:
        stmt = stmt.where(RecurringTransaction.is_active.is_(is_active))
    return list(session.scalars(stmt.order_by(RecurringTransaction.id)))


def get_audit_log(session: Session, user_id: int, audit_log_id: int) -> AuditLog | None:
    """Ambil satu entri audit log milik `user_id`."""
    return session.scalars(
        select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.id == audit_log_id)
    ).first()


def list_audit_logs(
    session: Session,
    user_id: int,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    action: str | None = None,
    limit: int | None = 50,
) -> list[AuditLog]:
    """Daftar entri audit log milik `user_id`, terbaru lebih dulu."""
    stmt = select(AuditLog).where(AuditLog.user_id == user_id)
    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def get_last_undoable_log(session: Session, user_id: int) -> AuditLog | None:
    """Ambil entri audit log terakhir milik `user_id` yang belum di-undo."""
    return session.scalars(
        select(AuditLog)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.is_undone.is_(False),
            AuditLog.action != "undo",
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).first()


def get_all_user_data(session: Session, user_id: int) -> dict[str, list[Any]]:
    """Ambil seluruh entitas data milik user_id untuk keperluan backup."""
    methods = list(
        session.scalars(
            select(PaymentMethod).where(PaymentMethod.user_id == user_id).order_by(PaymentMethod.id)
        )
    )
    categories = list(
        session.scalars(select(Category).where(Category.user_id == user_id).order_by(Category.id))
    )
    openings = list(
        session.scalars(
            select(OpeningBalance)
            .where(OpeningBalance.user_id == user_id)
            .order_by(OpeningBalance.id)
        )
    )
    budgets = list(
        session.scalars(select(Budget).where(Budget.user_id == user_id).order_by(Budget.id))
    )
    weekly_budgets = list(
        session.scalars(
            select(WeeklyBudget).where(WeeklyBudget.user_id == user_id).order_by(WeeklyBudget.id)
        )
    )
    recurrings = list(
        session.scalars(
            select(RecurringTransaction)
            .where(RecurringTransaction.user_id == user_id)
            .order_by(RecurringTransaction.id)
        )
    )
    transactions = list(
        session.scalars(
            select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.id)
        )
    )
    quick_aliases = list(
        session.scalars(
            select(QuickAlias).where(QuickAlias.user_id == user_id).order_by(QuickAlias.id)
        )
    )
    reminder_settings = list(
        session.scalars(
            select(ReminderSetting)
            .where(ReminderSetting.user_id == user_id)
            .order_by(ReminderSetting.id)
        )
    )
    return {
        "payment_methods": methods,
        "categories": categories,
        "opening_balances": openings,
        "budgets": budgets,
        "weekly_budgets": weekly_budgets,
        "recurring": recurrings,
        "transactions": transactions,
        "quick_aliases": quick_aliases,
        "reminder_settings": reminder_settings,
    }


def purge_user_data(session: Session, user_id: int) -> None:
    """Hapus seluruh data keuangan milik user_id untuk proses restore.

    Data pengguna lain tidak terpengaruh sama sekali.
    """
    for al in session.scalars(select(AuditLog).where(AuditLog.user_id == user_id)):
        session.delete(al)
    session.flush()

    dep_txs = list(
        session.scalars(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.related_transaction_id.isnot(None),
            )
        )
    )
    for t in dep_txs:
        session.delete(t)
    session.flush()

    user_txs = list(session.scalars(select(Transaction).where(Transaction.user_id == user_id)))
    for t in user_txs:
        t.related_transaction_id = None
    session.flush()

    for t in user_txs:
        session.delete(t)
    session.flush()

    for r in session.scalars(
        select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
    ):
        session.delete(r)

    for b in session.scalars(select(Budget).where(Budget.user_id == user_id)):
        session.delete(b)

    for wb in session.scalars(select(WeeklyBudget).where(WeeklyBudget.user_id == user_id)):
        session.delete(wb)

    for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.user_id == user_id)):
        session.delete(ob)
    session.flush()

    for qa in session.scalars(select(QuickAlias).where(QuickAlias.user_id == user_id)):
        session.delete(qa)

    for rs in session.scalars(select(ReminderSetting).where(ReminderSetting.user_id == user_id)):
        session.delete(rs)

    for us in session.scalars(select(UserSetting).where(UserSetting.user_id == user_id)):
        session.delete(us)

    for sn in session.scalars(select(SentNotification).where(SentNotification.user_id == user_id)):
        session.delete(sn)

    for c in session.scalars(
        select(Category).where(Category.user_id == user_id, Category.is_system.is_(False))
    ):
        session.delete(c)

    for m in session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user_id)):
        session.delete(m)
    session.flush()


# --- Quick Aliases -----------------------------------------------------------
def get_quick_alias(session: Session, user_id: int, alias_id: int) -> QuickAlias | None:
    """Ambil alias input cepat milik `user_id` berdasarkan ID."""
    return session.scalars(
        select(QuickAlias).where(QuickAlias.user_id == user_id, QuickAlias.id == alias_id)
    ).first()


def get_quick_alias_by_name(session: Session, user_id: int, alias: str) -> QuickAlias | None:
    """Ambil alias input cepat milik `user_id` berdasarkan teks alias (case-insensitive)."""
    return session.scalars(
        select(QuickAlias).where(
            QuickAlias.user_id == user_id,
            func.lower(QuickAlias.alias) == alias.strip().lower(),
        )
    ).first()


def list_quick_aliases(
    session: Session, user_id: int, target_type: str | None = None
) -> list[QuickAlias]:
    """Daftar seluruh alias input cepat milik `user_id`, terurut abjad."""
    stmt = select(QuickAlias).where(QuickAlias.user_id == user_id)
    if target_type is not None:
        stmt = stmt.where(QuickAlias.target_type == target_type)
    return list(session.scalars(stmt.order_by(QuickAlias.alias.asc())))


# --- Reminder Settings & User Settings (V2 & V3) -----------------------------
def get_reminder_setting(session: Session, user_id: int) -> ReminderSetting | None:
    """Ambil pengaturan pengingat milik `user_id`."""
    return session.scalars(
        select(ReminderSetting).where(ReminderSetting.user_id == user_id)
    ).first()


def list_active_reminder_settings(
    session: Session, user_id: int | None = None
) -> list[ReminderSetting]:
    """Daftar pengaturan pengingat pengguna aktif yang memiliki setidaknya satu notifikasi aktif."""
    stmt = (
        select(ReminderSetting)
        .join(User, User.id == ReminderSetting.user_id)
        .where(
            User.status == USER_STATUS_ACTIVE,
            or_(
                ReminderSetting.is_daily_active.is_(True),
                ReminderSetting.is_weekly_active.is_(True),
                ReminderSetting.is_monthly_active.is_(True),
            ),
        )
    )
    if user_id is not None:
        stmt = stmt.where(ReminderSetting.user_id == user_id)
    return list(session.scalars(stmt.order_by(ReminderSetting.id.asc())))


def get_user_setting(session: Session, user_id: int) -> UserSetting | None:
    """Ambil pengaturan pengguna V3 (`user_settings`) milik `user_id`."""
    return session.scalars(select(UserSetting).where(UserSetting.user_id == user_id)).first()


def list_active_user_settings(session: Session, user_id: int | None = None) -> list[UserSetting]:
    """Daftar pengaturan pengguna aktif yang memiliki setidaknya satu notifikasi terjadwal aktif."""
    stmt = (
        select(UserSetting)
        .join(User, User.id == UserSetting.user_id)
        .where(
            User.status == USER_STATUS_ACTIVE,
            or_(
                UserSetting.is_daily_active.is_(True),
                UserSetting.is_recurring_active.is_(True),
                UserSetting.is_weekly_active.is_(True),
                UserSetting.is_monthly_active.is_(True),
            ),
        )
    )
    if user_id is not None:
        stmt = stmt.where(UserSetting.user_id == user_id)
    return list(session.scalars(stmt.order_by(UserSetting.id.asc())))


def get_sent_notification(
    session: Session,
    user_id: int,
    notification_type: str,
    period_key: str,
) -> SentNotification | None:
    """Ambil catatan pengiriman notifikasi berdasarkan user_id, tipe, dan kunci periode."""
    return session.scalars(
        select(SentNotification).where(
            SentNotification.user_id == user_id,
            SentNotification.notification_type == notification_type,
            SentNotification.period_key == period_key,
        )
    ).first()


def list_sent_notifications(
    session: Session,
    user_id: int,
    notification_type: str | None = None,
) -> list[SentNotification]:
    """Daftar catatan notifikasi terkirim milik `user_id`."""
    stmt = select(SentNotification).where(SentNotification.user_id == user_id)
    if notification_type is not None:
        stmt = stmt.where(SentNotification.notification_type == notification_type)
    return list(session.scalars(stmt.order_by(SentNotification.id.desc())))


# --- Invites (V3) ------------------------------------------------------------
def get_invite(session: Session, user_id: int, invite_id: int) -> Invite | None:
    """Ambil satu kode undangan yang dibuat oleh `user_id`."""
    return session.scalars(
        select(Invite).where(Invite.user_id == user_id, Invite.id == invite_id)
    ).first()


def get_invite_by_code(session: Session, code: str) -> Invite | None:
    """Ambil kode undangan berdasarkan string kode (case-sensitive setelah trim)."""
    clean_code = code.strip()
    if not clean_code:
        return None
    return session.scalars(select(Invite).where(Invite.code == clean_code)).first()


def list_invites(session: Session, user_id: int, *, include_used: bool = True) -> list[Invite]:
    """Daftar kode undangan yang dibuat oleh `user_id`, terbaru lebih dulu."""
    stmt = select(Invite).where(Invite.user_id == user_id)
    if not include_used:
        stmt = stmt.where(Invite.is_used.is_(False))
    return list(session.scalars(stmt.order_by(Invite.id.desc())))


def list_all_invites(session: Session) -> list[Invite]:
    """Daftar seluruh kode undangan di sistem (untuk cadangan penuh admin)."""
    return list(session.scalars(select(Invite).order_by(Invite.id.desc())))


def delete_user_permanently(session: Session, user_id: int) -> None:
    """Hapus seluruh data pengguna beserta akun User itu sendiri secara permanen.

    Memperhatikan foreign keys dan relasi self-referential pada transactions:
    menghapus transaksi turunan (biaya admin transfer) lebih dahulu, melepas
    referensi tersisa, lalu menghapus transaksi, anggaran, saldo, kategori,
    undangan, dan entitas pengguna. Data pengguna lain tidak tersentuh sama sekali.
    """
    user = session.get(User, user_id)
    if user is None:
        return

    # 1. Audit log dan catatan notifikasi
    for al in session.scalars(select(AuditLog).where(AuditLog.user_id == user_id)):
        session.delete(al)
    for sn in session.scalars(select(SentNotification).where(SentNotification.user_id == user_id)):
        session.delete(sn)
    session.flush()

    # 2. Transaksi: Hapus transaksi turunan (biaya admin yang merujuk transaksi lain) lebih dulu
    dep_txs = list(
        session.scalars(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.related_transaction_id.isnot(None),
            )
        )
    )
    for t in dep_txs:
        session.delete(t)
    session.flush()

    # Hapus sisa transaksi milik pengguna
    rem_txs = list(session.scalars(select(Transaction).where(Transaction.user_id == user_id)))
    for t in rem_txs:
        t.related_transaction_id = None
    session.flush()
    for t in rem_txs:
        session.delete(t)
    session.flush()

    # 3. Transaksi rutin, anggaran, saldo awal, alias cepat, pengaturan
    for r in session.scalars(
        select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
    ):
        session.delete(r)
    for b in session.scalars(select(Budget).where(Budget.user_id == user_id)):
        session.delete(b)
    for wb in session.scalars(select(WeeklyBudget).where(WeeklyBudget.user_id == user_id)):
        session.delete(wb)
    for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.user_id == user_id)):
        session.delete(ob)
    for qa in session.scalars(select(QuickAlias).where(QuickAlias.user_id == user_id)):
        session.delete(qa)
    for rs in session.scalars(select(ReminderSetting).where(ReminderSetting.user_id == user_id)):
        session.delete(rs)
    for us in session.scalars(select(UserSetting).where(UserSetting.user_id == user_id)):
        session.delete(us)
    session.flush()

    # 4. Kategori (termasuk is_system) dan metode pembayaran
    for c in session.scalars(select(Category).where(Category.user_id == user_id)):
        session.delete(c)
    for m in session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user_id)):
        session.delete(m)
    session.flush()

    # 5. Undangan yang dibuat atau digunakan oleh pengguna
    for inv in session.scalars(select(Invite).where(Invite.user_id == user_id)):
        session.delete(inv)
    for inv in session.scalars(select(Invite).where(Invite.used_by_user_id == user_id)):
        inv.used_by_user_id = None
        inv.is_used = False
    session.flush()

    # 6. Hapus entitas pengguna
    session.delete(user)
    session.flush()


def get_database_diagnostics(session: Session) -> dict[str, str]:
    """Ambil status integritas dan mode jurnal SQLite untuk keperluan diagnostik operasional."""
    try:
        integrity = session.execute(text("PRAGMA integrity_check")).scalar() or "unknown"
    except Exception as exc:
        integrity = f"error: {exc}"

    try:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar() or "unknown"
    except Exception as exc:
        journal_mode = f"error: {exc}"

    return {"integrity": str(integrity), "journal_mode": str(journal_mode).upper()}


def count_all_transactions(session: Session) -> int:
    """Hitung seluruh transaksi di sistem."""
    return session.query(Transaction).count()


def count_all_budgets(session: Session) -> int:
    """Hitung seluruh entitas budget di sistem."""
    return session.query(Budget).count()


def count_all_recurring(session: Session) -> int:
    """Hitung seluruh transaksi rutin di sistem."""
    return session.query(RecurringTransaction).count()
