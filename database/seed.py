"""Data awal (seed): pengguna lokal, metode pembayaran, dan kategori default.

Semua fungsi idempoten: aman dijalankan berulang kali tanpa membuat data ganda.
Pemanggil bertanggung jawab atas commit (lewat `session_scope`).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import TYPE_EXPENSE, TYPE_INCOME, Category, PaymentMethod, User

DEFAULT_USER_NAME = "Pengguna"

DEFAULT_PAYMENT_METHODS = ("Cash", "Bank", "E-Wallet")

DEFAULT_EXPENSE_CATEGORIES = (
    "Makanan",
    "Transportasi",
    "Kos",
    "Pendidikan",
    "Tagihan",
    "Belanja",
    "Hiburan",
    "Kesehatan",
    "Kebutuhan Pribadi",
    "Lainnya",
)
DEFAULT_INCOME_CATEGORIES = (
    "Gaji",
    "Uang Saku",
    "Beasiswa",
    "Bonus",
    "Freelance",
    "Penjualan",
    "Lainnya",
)

# Kategori sistem untuk biaya admin transfer; tidak untuk dipilih manual.
ADMIN_FEE_CATEGORY = "Biaya Admin"


def ensure_default_user(session: Session, name: str = DEFAULT_USER_NAME) -> User:
    """Ambil pengguna pertama, atau buat satu jika tabel masih kosong (V1: satu pengguna)."""
    user = session.scalars(select(User).order_by(User.id)).first()
    if user is None:
        user = User(name=name)
        session.add(user)
        session.flush()
    return user


def seed_defaults(session: Session, user_id: int) -> None:
    """Buat metode pembayaran dan kategori default untuk `user_id` yang belum ada."""
    existing_methods = set(
        session.scalars(select(PaymentMethod.name).where(PaymentMethod.user_id == user_id))
    )
    for name in DEFAULT_PAYMENT_METHODS:
        if name not in existing_methods:
            session.add(PaymentMethod(user_id=user_id, name=name))

    existing_categories = {
        (row.type, row.name)
        for row in session.execute(
            select(Category.type, Category.name).where(Category.user_id == user_id)
        )
    }
    wanted = (
        [(TYPE_EXPENSE, name, False) for name in DEFAULT_EXPENSE_CATEGORIES]
        + [(TYPE_INCOME, name, False) for name in DEFAULT_INCOME_CATEGORIES]
        + [(TYPE_EXPENSE, ADMIN_FEE_CATEGORY, True)]
    )
    for category_type, name, is_system in wanted:
        if (category_type, name) not in existing_categories:
            session.add(
                Category(user_id=user_id, name=name, type=category_type, is_system=is_system)
            )
    session.flush()
