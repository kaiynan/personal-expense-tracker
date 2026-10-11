"""Service saldo awal: satu baris per metode, beserta tanggal berlakunya.

Transaksi bertanggal sebelum tanggal berlaku tidak dihitung dalam saldo
(dianggap sudah termasuk di saldo awal).
"""

from sqlalchemy.orm import Session

from database import queries
from models import OpeningBalance
from services.common import (
    balance_snapshot,
    negative_balance_warnings,
    require_user,
    resolve_date,
)
from services.results import OpeningBalanceResult
from utils.validators import validate_balance, validate_method


def set_opening_balance(
    session: Session,
    user_id: int,
    method_id: int,
    amount: object,
    effective_date: object | None = None,
) -> OpeningBalanceResult:
    """Buat atau ganti saldo awal sebuah metode.

    Args:
        amount: saldo awal (>= 0).
        effective_date: `date` atau teks DD-MM-YYYY; default hari ini.

    Mengganti saldo awal bisa membuat saldo metode minus; itu hanya peringatan.
    """
    user = require_user(session, user_id)
    method = validate_method(session, user_id, method_id)
    amount = validate_balance(amount, "Saldo awal")
    effective = resolve_date(user, effective_date)

    before = balance_snapshot(session, user_id)
    opening = queries.get_opening_balance(session, user_id, method.id)
    if opening is None:
        opening = OpeningBalance(
            user_id=user_id, method_id=method.id, amount=amount, effective_date=effective
        )
        session.add(opening)
    else:
        opening.amount = amount
        opening.effective_date = effective

    warnings = negative_balance_warnings(session, user_id, before, [method.id])
    return OpeningBalanceResult(opening, warnings)


def get_opening_balance(session: Session, user_id: int, method_id: int) -> OpeningBalance | None:
    """Saldo awal metode milik `user_id`, atau `None` bila belum diatur."""
    return queries.get_opening_balance(session, user_id, method_id)
