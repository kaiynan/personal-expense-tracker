"""Fixture bersama: database SQLite in-memory dengan skema dari model."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from database.engine import create_db_engine
from models import Base, OpeningBalance, PaymentMethod, Transaction, User


@pytest.fixture
def engine():
    """Engine SQLite in-memory (PRAGMA foreign_keys aktif, sama seperti aplikasi)."""
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """Session untuk satu tes. Factory di bawah memakai commit agar rollback
    akibat IntegrityError hanya membatalkan baris yang salah."""
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def make_user(session):
    """Pabrik pengguna."""

    def _make(name: str = "Tester", telegram_id: int | None = None) -> User:
        user = User(name=name, telegram_id=telegram_id)
        session.add(user)
        session.commit()
        return user

    return _make


@pytest.fixture
def make_method(session):
    """Pabrik metode pembayaran, opsional dengan saldo awal."""

    def _make(
        user: User,
        name: str,
        opening: int | None = None,
        effective: date = date(2026, 1, 1),
        archived: bool = False,
    ) -> PaymentMethod:
        method = PaymentMethod(user_id=user.id, name=name, is_archived=archived)
        session.add(method)
        session.commit()
        if opening is not None:
            session.add(
                OpeningBalance(
                    user_id=user.id,
                    method_id=method.id,
                    amount=opening,
                    effective_date=effective,
                )
            )
            session.commit()
        return method

    return _make


@pytest.fixture
def add_tx(session):
    """Pabrik transaksi."""

    def _add(
        user: User,
        type_: str,
        amount: int,
        method: PaymentMethod,
        on: date = date(2026, 2, 1),
        to: PaymentMethod | None = None,
        status: str = "active",
    ) -> Transaction:
        tx = Transaction(
            user_id=user.id,
            type=type_,
            amount=amount,
            date=on,
            method_id=method.id,
            to_method_id=to.id if to else None,
            status=status,
        )
        session.add(tx)
        session.commit()
        return tx

    return _add


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset rate limiter per-test agar tidak memblokir uji coba berulang."""
    from utils.rate_limiter import global_rate_limiter

    global_rate_limiter.reset()
