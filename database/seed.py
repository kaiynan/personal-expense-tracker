"""Data awal (seed): pengguna lokal, metode pembayaran, dan kategori default.

Semua fungsi idempoten: aman dijalankan berulang kali tanpa membuat data ganda.
Pemanggil bertanggung jawab atas commit (lewat `session_scope`).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    TARGET_CATEGORY,
    TARGET_PAYMENT_METHOD,
    TYPE_EXPENSE,
    TYPE_INCOME,
    Category,
    PaymentMethod,
    QuickAlias,
    ReminderSetting,
    User,
    UserSetting,
)

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

    seed_default_aliases(session, user_id)

    existing_setting = session.scalars(
        select(ReminderSetting).where(ReminderSetting.user_id == user_id)
    ).first()
    if existing_setting is None:
        session.add(ReminderSetting(user_id=user_id))

    existing_user_setting = session.scalars(
        select(UserSetting).where(UserSetting.user_id == user_id)
    ).first()
    if existing_user_setting is None:
        user_obj = session.scalars(select(User).where(User.id == user_id)).first()
        tz_val = user_obj.timezone if user_obj is not None else "Asia/Jakarta"
        session.add(UserSetting(user_id=user_id, timezone=tz_val))
    session.flush()


DEFAULT_CATEGORY_ALIASES: dict[str, str] = {
    "makan": "Makanan",
    "makanan": "Makanan",
    "food": "Makanan",
    "sarapan": "Makanan",
    "siang": "Makanan",
    "malam": "Makanan",
    "kopi": "Makanan",
    "snack": "Makanan",
    "transport": "Transportasi",
    "transportasi": "Transportasi",
    "bensin": "Transportasi",
    "ojol": "Transportasi",
    "parkir": "Transportasi",
    "kos": "Kos",
    "pendidikan": "Pendidikan",
    "buku": "Pendidikan",
    "kuliah": "Pendidikan",
    "tagihan": "Tagihan",
    "pulsa": "Tagihan",
    "kuota": "Tagihan",
    "listrik": "Tagihan",
    "wifi": "Tagihan",
    "internet": "Tagihan",
    "air": "Tagihan",
    "belanja": "Belanja",
    "hiburan": "Hiburan",
    "nonton": "Hiburan",
    "game": "Hiburan",
    "kesehatan": "Kesehatan",
    "obat": "Kesehatan",
    "dokter": "Kesehatan",
    "gaji": "Gaji",
    "uangsaku": "Uang Saku",
    "saku": "Uang Saku",
    "beasiswa": "Beasiswa",
    "bonus": "Bonus",
    "freelance": "Freelance",
    "penjualan": "Penjualan",
}

DEFAULT_METHOD_ALIASES: dict[str, str] = {
    "cash": "Cash",
    "tunai": "Cash",
    "uang": "Cash",
    "bank": "Bank",
    "bca": "Bank",
    "mandiri": "Bank",
    "bri": "Bank",
    "bni": "Bank",
    "jago": "Bank",
    "seabank": "Bank",
    "ewallet": "E-Wallet",
    "wallet": "E-Wallet",
    "gopay": "E-Wallet",
    "ovo": "E-Wallet",
    "dana": "E-Wallet",
    "shopeepay": "E-Wallet",
}


def seed_default_aliases(session: Session, user_id: int) -> int:
    """Buat alias input cepat bawaan jika belum ada untuk user_id.

    Returns:
        Jumlah alias baru yang berhasil ditambahkan.
    """
    cat_rows = session.execute(
        select(Category.id, Category.name).where(Category.user_id == user_id)
    ).all()
    cat_map = {name.lower(): cid for cid, name in cat_rows}

    method_rows = session.execute(
        select(PaymentMethod.id, PaymentMethod.name).where(PaymentMethod.user_id == user_id)
    ).all()
    method_map = {name.lower(): mid for mid, name in method_rows}

    existing_aliases = set(
        session.scalars(select(QuickAlias.alias).where(QuickAlias.user_id == user_id))
    )

    added = 0
    # 1. Alias Kategori
    for alias_text, cat_name in DEFAULT_CATEGORY_ALIASES.items():
        if alias_text not in existing_aliases:
            cid = cat_map.get(cat_name.lower())
            if cid is not None:
                session.add(
                    QuickAlias(
                        user_id=user_id,
                        alias=alias_text,
                        target_type=TARGET_CATEGORY,
                        category_id=cid,
                        method_id=None,
                    )
                )
                existing_aliases.add(alias_text)
                added += 1

    # 2. Alias Metode Pembayaran
    for alias_text, meth_name in DEFAULT_METHOD_ALIASES.items():
        if alias_text not in existing_aliases:
            mid = method_map.get(meth_name.lower())
            if mid is not None:
                session.add(
                    QuickAlias(
                        user_id=user_id,
                        alias=alias_text,
                        target_type=TARGET_PAYMENT_METHOD,
                        category_id=None,
                        method_id=mid,
                    )
                )
                existing_aliases.add(alias_text)
                added += 1

    session.flush()
    return added
