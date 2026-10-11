"""Service koreksi saldo (adjustment).

Pengguna memasukkan saldo sebenarnya; service membuat transaksi `adjustment`
sebesar selisih terhadap saldo tercatat pada tanggal koreksi (boleh negatif).
Adjustment memengaruhi saldo, tetapi tidak dihitung sebagai pemasukan/pengeluaran.
"""

from sqlalchemy.orm import Session

from database import queries
from database.balances import method_balances
from models import TYPE_ADJUSTMENT, Transaction
from services.common import (
    balance_snapshot,
    negative_balance_warnings,
    require_user,
    resolve_date,
)
from services.results import AdjustmentResult
from utils.dates import format_display
from utils.errors import ValidationError
from utils.validators import validate_balance, validate_method, validate_note


def adjust_balance(
    session: Session,
    user_id: int,
    method_id: int,
    actual_balance: object,
    on: object | None = None,
    note: str | None = None,
) -> AdjustmentResult:
    """Samakan saldo tercatat metode dengan `actual_balance` per tanggal `on`.

    Bila selisihnya 0, tidak ada transaksi yang dibuat (`transaction=None`).

    Berbeda dengan transaksi biasa, koreksi bertanggal sebelum tanggal berlaku
    saldo awal **ditolak**: koreksi itu tidak akan pernah dihitung dalam saldo,
    sehingga tidak bisa mencapai tujuannya.
    """
    user = require_user(session, user_id)
    method = validate_method(session, user_id, method_id)
    actual = validate_balance(actual_balance, "Saldo sebenarnya")
    tx_date = resolve_date(user, on)
    note = validate_note(note)

    opening = queries.get_opening_balance(session, user_id, method.id)
    if opening is not None and tx_date < opening.effective_date:
        raise ValidationError(
            f"Tanggal koreksi {format_display(tx_date)} sebelum tanggal berlaku saldo awal "
            f"{method.name} ({format_display(opening.effective_date)}). "
            "Ubah saldo awal saja, atau pilih tanggal yang lebih baru."
        )

    before = balance_snapshot(session, user_id)
    recorded = method_balances(session, user_id, as_of=tx_date).get(method.id, 0)
    difference = actual - recorded
    if difference == 0:
        return AdjustmentResult(None, recorded, actual, 0, [])

    tx = Transaction(
        user_id=user_id,
        type=TYPE_ADJUSTMENT,
        amount=difference,
        date=tx_date,
        method_id=method.id,
        note=note,
    )
    session.add(tx)
    # Saldo per tanggal koreksi pasti >= 0, tetapi transaksi bertanggal setelahnya
    # bisa membuat saldo keseluruhan tetap minus; itu diberi peringatan.
    warnings = negative_balance_warnings(session, user_id, before, [method.id])
    return AdjustmentResult(tx, recorded, actual, difference, warnings)
