"""Service transfer antar metode pembayaran, dengan opsi biaya admin.

Transfer adalah jenis transaksi tersendiri (bukan pemasukan/pengeluaran).
Biaya admin dicatat sebagai transaksi **pengeluaran terpisah** dari metode asal
dengan kategori sistem "Biaya Admin" dan terhubung ke transfer lewat
`related_transaction_id`.
"""

from sqlalchemy.orm import Session

from database import queries
from database.seed import ADMIN_FEE_CATEGORY
from models import TYPE_EXPENSE, TYPE_TRANSFER, Category, Transaction
from services.common import (
    balance_snapshot,
    before_opening_warnings,
    negative_balance_warnings,
    require_user,
    resolve_date,
)
from services.results import TransferResult
from utils.errors import ValidationError
from utils.validators import validate_amount, validate_balance, validate_method, validate_note


def _admin_fee_category(session: Session, user_id: int) -> Category:
    """Kategori sistem "Biaya Admin" milik pengguna; dibuat bila belum ada (belum di-seed)."""
    category = queries.get_system_category(session, user_id, ADMIN_FEE_CATEGORY, TYPE_EXPENSE)
    if category is None:
        category = Category(
            user_id=user_id, name=ADMIN_FEE_CATEGORY, type=TYPE_EXPENSE, is_system=True
        )
        session.add(category)
        session.flush()
    return category


def add_transfer(
    session: Session,
    user_id: int,
    amount: object,
    from_method_id: int,
    to_method_id: int,
    on: object | None = None,
    note: str | None = None,
    admin_fee: object | None = None,
) -> TransferResult:
    """Catat transfer dari `from_method_id` ke `to_method_id`.

    Args:
        admin_fee: biaya admin (opsional). `None` atau 0 berarti tanpa biaya.
            Dibebankan ke metode asal pada tanggal yang sama.
    """
    user = require_user(session, user_id)
    amount = validate_amount(amount, "Nominal transfer")
    fee = validate_balance(admin_fee, "Biaya admin") if admin_fee is not None else 0
    tx_date = resolve_date(user, on)
    note = validate_note(note)
    source = validate_method(session, user_id, from_method_id)
    target = validate_method(session, user_id, to_method_id)
    if source.id == target.id:
        raise ValidationError("Metode asal dan tujuan transfer tidak boleh sama.")
    fee_category = _admin_fee_category(session, user_id) if fee else None

    before = balance_snapshot(session, user_id)
    transfer = Transaction(
        user_id=user_id,
        type=TYPE_TRANSFER,
        amount=amount,
        date=tx_date,
        method_id=source.id,
        to_method_id=target.id,
        note=note,
    )
    session.add(transfer)

    fee_tx = None
    if fee_category is not None:
        session.flush()
        fee_tx = Transaction(
            user_id=user_id,
            type=TYPE_EXPENSE,
            amount=fee,
            date=tx_date,
            category_id=fee_category.id,
            method_id=source.id,
            note=f"Biaya admin transfer {source.name} ke {target.name}",
            related_transaction_id=transfer.id,
        )
        session.add(fee_tx)

    methods = [source.id, target.id]
    warnings = before_opening_warnings(session, user_id, tx_date, methods)
    warnings += negative_balance_warnings(session, user_id, before, methods)
    return TransferResult(transfer, fee_tx, warnings)
