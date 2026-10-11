"""Query baca data pengguna (selain perhitungan saldo, lihat `balances.py`).

Semua query ditulis dengan SQLAlchemy ORM (otomatis berparameter, portabel) dan
**selalu** memfilter `user_id`. Data milik pengguna lain diperlakukan sama
seperti data yang tidak ada (mengembalikan `None`), sehingga keberadaannya
tidak bocor.
"""

from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from models import (
    STATUS_ACTIVE,
    Category,
    OpeningBalance,
    PaymentMethod,
    Transaction,
    User,
)


def get_user(session: Session, user_id: int) -> User | None:
    """Ambil pengguna berdasarkan id."""
    return session.scalars(select(User).where(User.id == user_id)).first()


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
