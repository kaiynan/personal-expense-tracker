"""Service transaksi: tambah pemasukan/pengeluaran, edit, soft delete, pulihkan,
ambil, dan daftar.

Semua fungsi sinkron, menerima `Session` dan `user_id`, dan tidak melakukan
commit: pemanggil membungkusnya dengan `session_scope` agar satu operasi
bersifat atomik.
"""

from sqlalchemy.orm import Session

from database import queries
from models import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_RESTORE,
    ACTION_UPDATE,
    STATUS_ACTIVE,
    STATUS_DELETED,
    TRANSACTION_TYPES,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_TRANSFER,
    Transaction,
)
from services import audit
from services.common import (
    balance_snapshot,
    before_opening_warnings,
    check_daily_transaction_limit,
    negative_balance_warnings,
    newer_adjustment_warnings,
    require_user,
    resolve_date,
)
from services.results import TransactionResult
from utils.errors import NotFoundError, ValidationError
from utils.validators import (
    parse_date,
    validate_amount,
    validate_balance,
    validate_category,
    validate_method,
    validate_note,
)


# --- Tambah --------------------------------------------------------------------
def _add_categorized(
    session: Session,
    user_id: int,
    type_: str,
    amount: object,
    category_id: object,
    method_id: object,
    on: object | None,
    note: object | None,
) -> TransactionResult:
    """Tambah pemasukan atau pengeluaran (keduanya wajib berkategori)."""
    user = require_user(session, user_id)
    amount = validate_amount(amount)
    tx_date = resolve_date(user, on)
    check_daily_transaction_limit(session, user_id, tx_date, 1)
    note = validate_note(note)
    category = validate_category(session, user_id, category_id, type_)
    method = validate_method(session, user_id, method_id)

    before = balance_snapshot(session, user_id)
    tx = Transaction(
        user_id=user_id,
        type=type_,
        amount=amount,
        date=tx_date,
        category_id=category.id,
        method_id=method.id,
        note=note,
    )
    session.add(tx)
    session.flush()
    audit.record_audit_log(
        session,
        user_id,
        action=ACTION_CREATE,
        entity_type="transaction",
        entity_id=tx.id,
        new_values=audit._snapshot_transaction(tx),
        description=f"Tambah {type_} Rp{amount:,} ({category.name}) via {method.name}",
    )
    warnings = before_opening_warnings(session, user_id, tx_date, [method.id])
    warnings += negative_balance_warnings(session, user_id, before, [method.id])
    return TransactionResult(tx, warnings)


def add_income(
    session: Session,
    user_id: int,
    amount: object,
    category_id: int,
    method_id: int,
    on: object | None = None,
    note: str | None = None,
) -> TransactionResult:
    """Catat pemasukan. `on`: `date` atau teks DD-MM-YYYY; default hari ini."""
    return _add_categorized(session, user_id, TYPE_INCOME, amount, category_id, method_id, on, note)


def add_expense(
    session: Session,
    user_id: int,
    amount: object,
    category_id: int,
    method_id: int,
    on: object | None = None,
    note: str | None = None,
) -> TransactionResult:
    """Catat pengeluaran. Bisa memunculkan peringatan saldo minus (tetap tersimpan)."""
    return _add_categorized(
        session, user_id, TYPE_EXPENSE, amount, category_id, method_id, on, note
    )


# --- Ambil & daftar ------------------------------------------------------------
def get_transaction(
    session: Session, user_id: int, transaction_id: int, include_deleted: bool = False
) -> Transaction:
    """Ambil satu transaksi milik `user_id`; `NotFoundError` bila tidak ada."""
    tx = queries.get_transaction(session, user_id, transaction_id, include_deleted)
    if tx is None:
        raise NotFoundError(f"Transaksi #{transaction_id} tidak ditemukan.")
    return tx


def get_related_transactions(
    session: Session, user_id: int, parent_transaction_id: int, include_deleted: bool = False
) -> list[Transaction]:
    """Ambil transaksi yang terhubung ke `parent_transaction_id` milik `user_id`."""
    return queries.get_related_transactions(
        session, user_id, parent_transaction_id, include_deleted=include_deleted
    )


def list_transactions(
    session: Session,
    user_id: int,
    *,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    start: object | None = None,
    end: object | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    limit: int | None = None,
    min_amount: object | None = None,
    max_amount: object | None = None,
) -> list[Transaction]:
    """Daftar transaksi milik `user_id` (default hanya `active`), terbaru dulu.

    `start`/`end` menerima `date` atau teks DD-MM-YYYY (inklusif).
    `min_amount`/`max_amount` memfilter nominal transaksi (inklusif).
    """
    if type_ is not None and type_ not in TRANSACTION_TYPES:
        raise ValidationError(f"Jenis transaksi tidak dikenal: {type_!r}.")
    start_date = parse_date(start, "Tanggal awal") if start is not None else None
    end_date = parse_date(end, "Tanggal akhir") if end is not None else None
    if start_date and end_date and start_date > end_date:
        raise ValidationError("Tanggal awal tidak boleh setelah tanggal akhir.")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValidationError("Batas jumlah harus bilangan bulat positif.")
    keyword = keyword.strip() if keyword else None

    min_val = None
    if min_amount is not None:
        min_val = validate_balance(min_amount, "Nominal minimum")
    max_val = None
    if max_amount is not None:
        max_val = validate_balance(max_amount, "Nominal maksimum")
    if min_val is not None and max_val is not None and min_val > max_val:
        raise ValidationError("Nominal minimum tidak boleh lebih besar dari nominal maksimum.")

    return queries.list_transactions(
        session,
        user_id,
        type_=type_,
        category_id=category_id,
        method_id=method_id,
        start=start_date,
        end=end_date,
        keyword=keyword or None,
        include_deleted=include_deleted,
        limit=limit,
        min_amount=min_val,
        max_amount=max_val,
    )


# --- Edit ----------------------------------------------------------------------
def edit_transaction(
    session: Session,
    user_id: int,
    transaction_id: int,
    *,
    amount: object | None = None,
    on: object | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    to_method_id: int | None = None,
    note: str | None = None,
) -> TransactionResult:
    """Ubah transaksi aktif. Argumen `None` berarti "tidak diubah".

    - `note=""` mengosongkan keterangan.
    - Jenis transaksi tidak bisa diubah (hapus lalu buat baru).
    - Koreksi saldo (adjustment) tidak bisa diedit; hapus lalu buat koreksi baru.
    - Kategori/metode hanya divalidasi bila diganti, sehingga transaksi lama
      yang kategorinya kini diarsipkan tetap bisa diedit nominal/keterangannya.
    """
    tx = get_transaction(session, user_id, transaction_id, include_deleted=True)
    if tx.status != STATUS_ACTIVE:
        raise ValidationError(f"Transaksi #{tx.id} sudah dihapus; pulihkan dulu.")
    if tx.type == TYPE_ADJUSTMENT:
        raise ValidationError("Koreksi saldo tidak bisa diedit; hapus lalu buat koreksi baru.")
    if tx.related_transaction_id is not None:
        raise ValidationError(
            f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer; "
            "perubahan harus dilakukan lewat transfernya."
        )

    changes: dict[str, object] = {}
    if amount is not None:
        changes["amount"] = validate_amount(amount)
    if on is not None:
        changes["date"] = parse_date(on)
    if note is not None:
        changes["note"] = validate_note(note)
    if method_id is not None and method_id != tx.method_id:
        changes["method_id"] = validate_method(session, user_id, method_id).id

    if tx.type == TYPE_TRANSFER:
        if category_id is not None:
            raise ValidationError("Transfer tidak memakai kategori.")
        if to_method_id is not None and to_method_id != tx.to_method_id:
            changes["to_method_id"] = validate_method(session, user_id, to_method_id).id
        new_from = changes.get("method_id", tx.method_id)
        new_to = changes.get("to_method_id", tx.to_method_id)
        if new_from == new_to:
            raise ValidationError("Metode asal dan tujuan transfer tidak boleh sama.")
    else:
        if to_method_id is not None:
            raise ValidationError("Hanya transfer yang punya metode tujuan.")
        if category_id is not None and category_id != tx.category_id:
            changes["category_id"] = validate_category(session, user_id, category_id, tx.type).id

    if not changes:
        return TransactionResult(tx, [])

    related = queries.get_related_transactions(session, user_id, tx.id, include_deleted=False)
    affected = [tx.method_id, tx.to_method_id] + [
        m for rel in related for m in (rel.method_id, rel.to_method_id)
    ]
    before = balance_snapshot(session, user_id)
    old_snap = audit._snapshot_transaction(tx, related)
    for attr, value in changes.items():
        setattr(tx, attr, value)

    # Sinkronisasi transaksi terhubung (mis. biaya admin) jika tanggal atau metode asal berubah
    if "date" in changes:
        for rel in related:
            rel.date = tx.date
    if "method_id" in changes:
        for rel in related:
            rel.method_id = tx.method_id

    session.flush()
    new_snap = audit._snapshot_transaction(tx, related)
    audit.record_audit_log(
        session,
        user_id,
        action=ACTION_UPDATE,
        entity_type="transaction",
        entity_id=tx.id,
        old_values=old_snap,
        new_values=new_snap,
        description=f"Edit transaksi #{tx.id}",
    )

    affected += [tx.method_id, tx.to_method_id] + [
        m for rel in related for m in (rel.method_id, rel.to_method_id)
    ]

    warnings = []
    if {"date", "method_id", "to_method_id"} & changes.keys():
        warnings += before_opening_warnings(
            session, user_id, tx.date, [tx.method_id, tx.to_method_id]
        )
        for rel in related:
            if rel.date != tx.date:
                warnings += before_opening_warnings(
                    session, user_id, rel.date, [rel.method_id, rel.to_method_id]
                )
    warnings += newer_adjustment_warnings(
        session,
        user_id,
        min(tx.date, changes.get("date", tx.date)),
        affected,
        exclude_id=tx.id,
    )
    warnings += negative_balance_warnings(session, user_id, before, affected)
    return TransactionResult(tx, warnings)


# --- Hapus & pulihkan ----------------------------------------------------------
def delete_transaction(session: Session, user_id: int, transaction_id: int) -> TransactionResult:
    """Soft delete: ubah status menjadi `deleted`. Konfirmasi dilakukan di antarmuka.

    Menghapus pemasukan/transfer masuk bisa membuat saldo minus; itu diberi
    peringatan, tidak diblokir.
    """
    tx = get_transaction(session, user_id, transaction_id, include_deleted=True)
    if tx.status == STATUS_DELETED:
        raise ValidationError(f"Transaksi #{tx.id} sudah dihapus.")
    if tx.related_transaction_id is not None:
        raise ValidationError(
            f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer; "
            "penghapusan harus dilakukan lewat transfernya."
        )

    # Ambil transaksi terhubung aktif (mis. biaya admin transfer)
    related = queries.get_related_transactions(session, user_id, tx.id, include_deleted=False)
    affected = [tx.method_id, tx.to_method_id] + [
        m for rel in related for m in (rel.method_id, rel.to_method_id)
    ]

    before = balance_snapshot(session, user_id)
    old_snap = audit._snapshot_transaction(tx, related)
    tx.status = STATUS_DELETED
    for rel in related:
        rel.status = STATUS_DELETED

    session.flush()
    new_snap = audit._snapshot_transaction(tx, related)
    audit.record_audit_log(
        session,
        user_id,
        action=ACTION_DELETE,
        entity_type="transaction",
        entity_id=tx.id,
        old_values=old_snap,
        new_values=new_snap,
        description=f"Hapus transaksi #{tx.id}",
    )

    warnings = newer_adjustment_warnings(
        session,
        user_id,
        tx.date,
        affected,
        exclude_id=tx.id,
    )
    warnings += negative_balance_warnings(session, user_id, before, affected)
    return TransactionResult(tx, warnings)


def restore_transaction(session: Session, user_id: int, transaction_id: int) -> TransactionResult:
    """Pulihkan transaksi yang sudah di-soft-delete menjadi `active`."""
    tx = get_transaction(session, user_id, transaction_id, include_deleted=True)
    if tx.status != STATUS_DELETED:
        raise ValidationError(f"Transaksi #{tx.id} tidak dalam keadaan terhapus.")
    if tx.related_transaction_id is not None:
        raise ValidationError(
            f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer; "
            "pemulihan harus dilakukan lewat transfernya."
        )

    # Ambil transaksi terhubung yang terhapus untuk dipulihkan bersamaan
    related = [
        rel
        for rel in queries.get_related_transactions(session, user_id, tx.id, include_deleted=True)
        if rel.status == STATUS_DELETED
    ]

    before = balance_snapshot(session, user_id)
    old_snap = audit._snapshot_transaction(tx, related)
    tx.status = STATUS_ACTIVE
    for rel in related:
        rel.status = STATUS_ACTIVE

    session.flush()
    new_snap = audit._snapshot_transaction(tx, related)
    audit.record_audit_log(
        session,
        user_id,
        action=ACTION_RESTORE,
        entity_type="transaction",
        entity_id=tx.id,
        old_values=old_snap,
        new_values=new_snap,
        description=f"Pulihkan transaksi #{tx.id}",
    )

    methods = [tx.method_id, tx.to_method_id] + [
        m for rel in related for m in (rel.method_id, rel.to_method_id)
    ]
    warnings = before_opening_warnings(session, user_id, tx.date, [tx.method_id, tx.to_method_id])
    for rel in related:
        if rel.date != tx.date:
            warnings += before_opening_warnings(
                session, user_id, rel.date, [rel.method_id, rel.to_method_id]
            )
    warnings += newer_adjustment_warnings(
        session,
        user_id,
        tx.date,
        methods,
        exclude_id=tx.id,
    )
    warnings += negative_balance_warnings(session, user_id, before, methods)
    return TransactionResult(tx, warnings)
