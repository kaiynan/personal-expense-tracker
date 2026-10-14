"""Service kategori: kelola kategori kustom (tambah, ubah nama, arsipkan, pulihkan)."""

from sqlalchemy.orm import Session

from database import queries
from models import Category, PaymentMethod
from services.common import require_user
from utils.errors import NotFoundError, ValidationError
from utils.validators import validate_category_name, validate_category_type


def list_categories(
    session: Session,
    user_id: int,
    *,
    type_: str | None = None,
    include_archived: bool = False,
    include_system: bool = False,
) -> list[Category]:
    """Daftar kategori milik `user_id` (default tidak termasuk kategori sistem & arsip)."""
    return queries.list_categories(
        session,
        user_id,
        type_=type_,
        include_archived=include_archived,
        include_system=include_system,
    )


def get_category(session: Session, user_id: int, category_id: int) -> Category | None:
    """Ambil satu kategori milik `user_id`."""
    return queries.get_category(session, user_id, category_id)


def add_category(session: Session, user_id: int, name: str, type_: str) -> Category:
    """Tambah kategori kustom baru untuk `user_id`."""
    require_user(session, user_id)
    clean_name = validate_category_name(name)
    clean_type = validate_category_type(type_)

    existing = queries.get_category_by_name(session, user_id, clean_name, clean_type)
    if existing is not None:
        if existing.is_archived:
            raise ValidationError(
                f"Kategori '{existing.name}' ({clean_type}) sudah ada dalam arsip. "
                "Pulihkan kategori tersebut untuk menggunakannya kembali."
            )
        raise ValidationError(f"Kategori '{existing.name}' ({clean_type}) sudah ada.")

    category = Category(
        user_id=user_id,
        name=clean_name,
        type=clean_type,
        is_archived=False,
        is_system=False,
    )
    session.add(category)
    session.flush()
    return category


def rename_category(session: Session, user_id: int, category_id: int, new_name: str) -> Category:
    """Ubah nama kategori milik `user_id`."""
    require_user(session, user_id)
    category = queries.get_category(session, user_id, category_id)
    if category is None:
        raise NotFoundError("Kategori tidak ditemukan.")
    if category.is_system:
        raise ValidationError("Kategori sistem tidak boleh diubah namanya.")

    clean_name = validate_category_name(new_name)
    if clean_name == category.name:
        return category

    existing = queries.get_category_by_name(session, user_id, clean_name, category.type)
    if existing is not None and existing.id != category.id:
        if existing.is_archived:
            raise ValidationError(
                f"Kategori '{existing.name}' ({category.type}) sudah ada dalam arsip."
            )
        raise ValidationError(f"Kategori '{existing.name}' ({category.type}) sudah ada.")

    category.name = clean_name
    session.flush()
    return category


def archive_category(session: Session, user_id: int, category_id: int) -> Category:
    """Arsipkan kategori milik `user_id`."""
    require_user(session, user_id)
    category = queries.get_category(session, user_id, category_id)
    if category is None:
        raise NotFoundError("Kategori tidak ditemukan.")
    if category.is_system:
        raise ValidationError("Kategori sistem tidak boleh diarsipkan.")
    if category.is_archived:
        raise ValidationError(f"Kategori '{category.name}' sudah dalam keadaan diarsipkan.")

    category.is_archived = True
    session.flush()
    return category


def unarchive_category(session: Session, user_id: int, category_id: int) -> Category:
    """Pulihkan kategori yang diarsipkan milik `user_id`."""
    require_user(session, user_id)
    category = queries.get_category(session, user_id, category_id)
    if category is None:
        raise NotFoundError("Kategori tidak ditemukan.")
    if category.is_system:
        raise ValidationError("Kategori sistem tidak dapat diubah status arsipnya.")
    if not category.is_archived:
        raise ValidationError(f"Kategori '{category.name}' tidak dalam keadaan diarsipkan.")

    category.is_archived = False
    session.flush()
    return category


def list_payment_methods(
    session: Session,
    user_id: int,
    *,
    include_archived: bool = True,
) -> list[PaymentMethod]:
    """Daftar metode pembayaran milik `user_id`."""
    require_user(session, user_id)
    methods = queries.list_methods(session, user_id)
    if not include_archived:
        return [m for m in methods if not m.is_archived]
    return methods


def add_payment_method(session: Session, user_id: int, name: str) -> PaymentMethod:
    """Tambah metode pembayaran baru untuk `user_id`."""
    require_user(session, user_id)
    clean_name = validate_category_name(name)

    existing = queries.get_method_by_name(session, user_id, clean_name)
    if existing is not None:
        if existing.is_archived:
            raise ValidationError(
                f"Metode pembayaran '{existing.name}' sudah ada dalam arsip. "
                "Pulihkan metode pembayaran tersebut untuk menggunakannya kembali."
            )
        raise ValidationError(f"Metode pembayaran '{existing.name}' sudah ada.")

    method = PaymentMethod(
        user_id=user_id,
        name=clean_name,
        is_archived=False,
    )
    session.add(method)
    session.flush()
    return method


def rename_payment_method(
    session: Session, user_id: int, method_id: int, new_name: str
) -> PaymentMethod:
    """Ubah nama metode pembayaran milik `user_id`."""
    require_user(session, user_id)
    method = queries.get_method(session, user_id, method_id)
    if method is None:
        raise NotFoundError("Metode pembayaran tidak ditemukan.")

    clean_name = validate_category_name(new_name)
    if clean_name == method.name:
        return method

    existing = queries.get_method_by_name(session, user_id, clean_name)
    if existing is not None and existing.id != method.id:
        if existing.is_archived:
            raise ValidationError(f"Metode pembayaran '{existing.name}' sudah ada dalam arsip.")
        raise ValidationError(f"Metode pembayaran '{existing.name}' sudah ada.")

    method.name = clean_name
    session.flush()
    return method


def archive_payment_method(session: Session, user_id: int, method_id: int) -> PaymentMethod:
    """Arsipkan metode pembayaran milik `user_id`."""
    require_user(session, user_id)
    method = queries.get_method(session, user_id, method_id)
    if method is None:
        raise NotFoundError("Metode pembayaran tidak ditemukan.")
    if method.is_archived:
        raise ValidationError(f"Metode pembayaran '{method.name}' sudah dalam keadaan diarsipkan.")

    method.is_archived = True
    session.flush()
    return method


def unarchive_payment_method(session: Session, user_id: int, method_id: int) -> PaymentMethod:
    """Pulihkan metode pembayaran yang diarsipkan milik `user_id`."""
    require_user(session, user_id)
    method = queries.get_method(session, user_id, method_id)
    if method is None:
        raise NotFoundError("Metode pembayaran tidak ditemukan.")
    if not method.is_archived:
        raise ValidationError(f"Metode pembayaran '{method.name}' tidak dalam keadaan diarsipkan.")

    method.is_archived = False
    session.flush()
    return method
