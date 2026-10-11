"""Fixture untuk tes layer service: dua pengguna yang sudah di-seed.

Memakai fixture `session` dari `tests/conftest.py` (SQLite in-memory).
"""

from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy.orm import Session

from database.seed import ADMIN_FEE_CATEGORY, seed_defaults
from models import TYPE_EXPENSE, TYPE_INCOME, Category, PaymentMethod, User
from services.opening_balances import set_opening_balance

OPENING_DATE = date(2026, 1, 1)
D = date(2026, 2, 1)  # tanggal default transaksi di tes


@dataclass
class Ctx:
    """Pengguna beserta id metode dan kategori defaultnya."""

    session: Session
    user: User
    methods: dict[str, PaymentMethod]
    expense: dict[str, Category]
    income: dict[str, Category]

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def cash(self) -> int:
        return self.methods["Cash"].id

    @property
    def bank(self) -> int:
        return self.methods["Bank"].id

    @property
    def ewallet(self) -> int:
        return self.methods["E-Wallet"].id

    @property
    def food(self) -> int:
        return self.expense["Makanan"].id

    @property
    def salary(self) -> int:
        return self.income["Gaji"].id

    @property
    def admin_fee(self) -> int:
        return self.expense[ADMIN_FEE_CATEGORY].id

    def open(self, method_id: int, amount: int, effective: date = OPENING_DATE) -> None:
        """Atur saldo awal lewat service."""
        set_opening_balance(self.session, self.id, method_id, amount, effective)
        self.session.commit()


def _build(session: Session, name: str) -> Ctx:
    user = User(name=name)
    session.add(user)
    session.flush()
    seed_defaults(session, user.id)
    session.commit()
    methods = {m.name: m for m in session.query(PaymentMethod).filter_by(user_id=user.id)}
    cats = session.query(Category).filter_by(user_id=user.id).all()
    return Ctx(
        session=session,
        user=user,
        methods=methods,
        expense={c.name: c for c in cats if c.type == TYPE_EXPENSE},
        income={c.name: c for c in cats if c.type == TYPE_INCOME},
    )


@pytest.fixture
def alice(session) -> Ctx:
    return _build(session, "Alice")


@pytest.fixture
def bob(session) -> Ctx:
    return _build(session, "Bob")
